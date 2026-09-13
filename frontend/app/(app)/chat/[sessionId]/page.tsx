"use client";

/**
 * The real chat screen (Phase 7 module 6) -- replaces module 3's static
 * placeholder. Loads history once, then every new turn goes through
 * lib/stream.ts's SSE consumption of module 5's streaming endpoint. On
 * `done`, the real MessageRead (data.id, real timestamps, real status) is
 * what gets pushed into the settled list -- a page reload renders from
 * GET /sessions/{id}/messages alone and looks identical, since both paths
 * render through the same AssistantMessage component.
 */

import { useEffect, useRef, useState } from "react";
import { useParams } from "next/navigation";
import { toast } from "sonner";
import { ChatInput } from "@/components/chat/chat-input";
import { MessageList, type StreamingState } from "@/components/chat/message-list";
import { apiFetch, ApiError } from "@/lib/api";
import { streamMessage } from "@/lib/stream";
import type { MessageRead, SessionRead } from "@/lib/types";

export default function ChatPage() {
  const params = useParams<{ sessionId: string }>();
  const sessionId = params.sessionId;

  const [session, setSession] = useState<SessionRead | null>(null);
  const [messages, setMessages] = useState<MessageRead[]>([]);
  const [streaming, setStreaming] = useState<StreamingState | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const streamingTextRef = useRef("");

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [sessionResult, historyResult] = await Promise.all([
          apiFetch<SessionRead>(`/sessions/${sessionId}`),
          apiFetch<MessageRead[]>(`/sessions/${sessionId}/messages`),
        ]);
        if (cancelled) return;
        setSession(sessionResult);
        setMessages(historyResult);
      } catch (err) {
        if (!cancelled) {
          setLoadError(err instanceof ApiError ? err.message : "Could not load this session.");
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  async function handleSend(content: string) {
    const optimisticUser: MessageRead = {
      id: `pending-${Date.now()}`,
      session_id: sessionId,
      role: "user",
      content,
      status: null,
      sources: null,
      verification: null,
      readability: null,
      config_version: null,
      latency_ms: null,
      created_at: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, optimisticUser]);
    streamingTextRef.current = "";
    setStreaming({ sources: null, text: "" });

    await streamMessage(sessionId, content, {
      onSources: (data) => setStreaming((prev) => (prev ? { ...prev, sources: data.sources } : prev)),
      onToken: (data) => {
        streamingTextRef.current += data.text;
        setStreaming((prev) => (prev ? { ...prev, text: streamingTextRef.current } : prev));
      },
      onDone: (data) => {
        setMessages((prev) => [...prev, data]);
        setStreaming(null);
      },
      onError: (data) => {
        toast.error(data.message || "Something went wrong. Please try again.");
        setStreaming(null);
      },
    });
  }

  if (loadError) {
    return (
      <div className="flex flex-1 items-center justify-center p-6">
        <p className="text-sm text-destructive">{loadError}</p>
      </div>
    );
  }

  return (
    <div className="flex flex-1 flex-col">
      <div className="border-b p-3">
        <p className="text-sm font-medium">{session?.title ?? "New conversation"}</p>
        <p className="text-xs text-muted-foreground">{session ? `Class ${session.grade}` : "Loading…"}</p>
      </div>
      <MessageList messages={messages} streaming={streaming} />
      <ChatInput disabled={streaming !== null} onSend={handleSend} />
    </div>
  );
}
