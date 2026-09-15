"use client";

/**
 * The real chat screen (Phase 7 module 6) -- replaces module 3's static
 * placeholder. Loads history once, then every new turn goes through
 * lib/stream.ts's SSE consumption of module 5's streaming endpoint. On
 * `done`, the real MessageRead (data.id, real timestamps, real status) is
 * what gets pushed into the settled list -- a page reload renders from
 * GET /sessions/{id}/messages alone and looks identical, since both paths
 * render through the same AssistantMessage component.
 *
 * Why sessionId "new" is a draft, not a real session: "New chat" used to
 * call POST /sessions immediately on landing (module 4's now-deleted /new
 * page), so clicking it repeatedly without ever typing anything piled up
 * empty "New conversation" rows forever -- reported live as a bug. Fixed by
 * never creating a session until handleSend actually fires: the literal
 * string "new" is just another value for this existing [sessionId] route,
 * meaning "nothing exists yet," and POST /sessions only happens at
 * first-message time, exactly once, exactly when it's actually needed --
 * same idea as ChatGPT/Claude's own "New chat" never touching the sidebar
 * until you send something.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { toast } from "sonner";
import { ChatInput } from "@/components/chat/chat-input";
import { MessageList, type StreamingState } from "@/components/chat/message-list";
import { apiFetch, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { streamMessage } from "@/lib/stream";
import type { MessageRead, SessionRead } from "@/lib/types";

export default function ChatPage() {
  const params = useParams<{ sessionId: string }>();
  const sessionId = params.sessionId;
  const router = useRouter();
  const { user } = useAuth();

  const [session, setSession] = useState<SessionRead | null>(null);
  const [messages, setMessages] = useState<MessageRead[]>([]);
  const [streaming, setStreaming] = useState<StreamingState | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const streamingTextRef = useRef("");

  // No special-casing needed here for the "new" -> real-id transition
  // handleSend triggers: it only calls router.replace() once the whole
  // exchange is already durably saved server-side (see handleSend), so by
  // the time this effect re-runs for the real id, a plain fetch already
  // gets the complete, correct history. An earlier version tried to skip
  // that refetch with a ref set before the fetch started -- which broke in
  // a subtler way: the ref persisted across React Strict Mode's dev-only
  // double-invoke of this effect, so the second (kept) invocation saw it
  // already set and returned immediately, while the first invocation's own
  // fetch got its result discarded by the cancelled-flag cleanup below --
  // net effect, neither invocation ever applied a result. Simpler and
  // correct: just let every sessionId change fetch normally.
  useEffect(() => {
    if (sessionId === "new") {
      setSession(null);
      setMessages([]);
      setLoadError(null);
      return;
    }

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

  const handleSend = useCallback(
    async (content: string) => {
      let activeSessionId = sessionId;
      let createdSession: SessionRead | null = null;

      if (activeSessionId === "new") {
        try {
          createdSession = await apiFetch<SessionRead>("/sessions", { method: "POST" });
          activeSessionId = createdSession.id;
          setSession(createdSession);
        } catch (err) {
          toast.error(err instanceof ApiError ? err.message : "Could not start a new chat. Please try again.");
          return;
        }
      }

      const optimisticUser: MessageRead = {
        id: `pending-${Date.now()}`,
        session_id: activeSessionId,
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

      await streamMessage(activeSessionId, content, {
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

      // Only swap the URL once the whole exchange is already durably saved
      // server-side (the streaming endpoint's "done" event -- streamMessage
      // resolves after that -- fires only once the answer is committed;
      // see api/sessions.py). Doing this earlier, right after creating the
      // session, raced a full remount of this page against the in-flight
      // send: confirmed live via network trace that Next.js does not
      // preserve this component's state across a router.replace() that
      // changes the [sessionId] segment, so any refs/state set beforehand
      // are gone, and a freshly mounted instance's own history fetch could
      // catch the session before the message was saved -- rendering empty
      // and never fetching again. Replacing only now means even a fresh
      // mount's fetch gets the complete, correct history immediately.
      if (createdSession) {
        router.replace(`/chat/${createdSession.id}`, { scroll: false });
      }
    },
    [sessionId, router],
  );

  if (loadError) {
    return (
      <div className="flex flex-1 items-center justify-center p-6">
        <p className="text-sm text-destructive">{loadError}</p>
      </div>
    );
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* The bar spans full width (so its border/background reaches the
          edges), but its content is centered in the same max-w-3xl reading
          column as the messages and input below -- otherwise the header
          would look inconsistent with a centered body on a wide screen. */}
      <div className="border-b bg-card/80 backdrop-blur-sm">
        <div className="mx-auto flex w-full max-w-3xl items-center justify-between p-4">
          <p className="text-base font-medium">{session?.title ?? "New conversation"}</p>
          {(session?.grade ?? user?.grade) !== undefined && (
            <span className="rounded-full bg-secondary px-2.5 py-1 text-sm font-medium text-secondary-foreground">
              Class {session?.grade ?? user?.grade}
            </span>
          )}
        </div>
      </div>
      <MessageList messages={messages} streaming={streaming} />
      <ChatInput disabled={streaming !== null} onSend={handleSend} />
    </div>
  );
}
