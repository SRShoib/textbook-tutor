/**
 * Consumes POST /sessions/{id}/messages/stream. The browser's native
 * EventSource can't POST, so this is a fetch() + manual ReadableStream read,
 * hand-parsing the `event: kind\ndata: {...}\n\n` blocks api/sessions.py
 * writes (_sse() there) -- a small, one-purpose helper, not a new SSE
 * library. Reuses lib/api.ts's token/refresh machinery rather than a
 * parallel auth path.
 */

import { API_BASE, API_PREFIX, getAccessToken, refreshSession } from "./api";
import type { StreamEvent } from "./types";

export interface StreamHandlers {
  onRetrieving?: (data: Extract<StreamEvent, { kind: "retrieving" }>["data"]) => void;
  onSources?: (data: Extract<StreamEvent, { kind: "sources" }>["data"]) => void;
  onToken?: (data: Extract<StreamEvent, { kind: "token" }>["data"]) => void;
  onVerification?: (data: Extract<StreamEvent, { kind: "verification" }>["data"]) => void;
  onDone?: (data: Extract<StreamEvent, { kind: "done" }>["data"]) => void;
  onError?: (data: Extract<StreamEvent, { kind: "error" }>["data"]) => void;
}

function parseBlock(block: string): StreamEvent | null {
  const lines = block.split("\n");
  const eventLine = lines.find((l) => l.startsWith("event: "));
  const dataLine = lines.find((l) => l.startsWith("data: "));
  if (!eventLine || !dataLine) return null;
  const kind = eventLine.slice("event: ".length);
  const data = JSON.parse(dataLine.slice("data: ".length));
  return { kind, data } as StreamEvent;
}

function dispatch(event: StreamEvent, handlers: StreamHandlers): void {
  switch (event.kind) {
    case "retrieving":
      handlers.onRetrieving?.(event.data);
      break;
    case "sources":
      handlers.onSources?.(event.data);
      break;
    case "token":
      handlers.onToken?.(event.data);
      break;
    case "verification":
      handlers.onVerification?.(event.data);
      break;
    case "done":
      handlers.onDone?.(event.data);
      break;
    case "error":
      handlers.onError?.(event.data);
      break;
  }
}

async function doStreamFetch(sessionId: string, content: string): Promise<Response> {
  const token = getAccessToken();
  return fetch(`${API_BASE}${API_PREFIX}/sessions/${sessionId}/messages/stream`, {
    method: "POST",
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({ content }),
  });
}

export async function streamMessage(sessionId: string, content: string, handlers: StreamHandlers): Promise<void> {
  let res = await doStreamFetch(sessionId, content);

  if (res.status === 401) {
    const refreshed = await refreshSession();
    if (refreshed) {
      res = await doStreamFetch(sessionId, content);
    }
  }

  if (!res.ok || !res.body) {
    handlers.onError?.({ code: "STREAM_REQUEST_FAILED", message: `Request failed with status ${res.status}` });
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let separatorIndex: number;
    while ((separatorIndex = buffer.indexOf("\n\n")) !== -1) {
      const block = buffer.slice(0, separatorIndex);
      buffer = buffer.slice(separatorIndex + 2);
      const event = parseBlock(block);
      if (event) dispatch(event, handlers);
    }
  }
}
