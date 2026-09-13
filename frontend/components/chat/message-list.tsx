"use client";

import { useEffect, useRef } from "react";
import type { MessageRead, SourceCitation } from "@/lib/types";
import { AssistantMessage } from "./assistant-message";
import { StreamingMessage } from "./streaming-message";

export interface StreamingState {
  sources: SourceCitation[] | null;
  text: string;
}

export function MessageList({
  messages,
  streaming,
}: {
  messages: MessageRead[];
  streaming: StreamingState | null;
}) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" });
  }, [messages, streaming]);

  return (
    <div className="flex flex-1 flex-col gap-4 overflow-y-auto p-4">
      {messages.map((message) =>
        message.role === "user" ? (
          <div key={message.id} className="ml-auto max-w-lg rounded-2xl bg-primary px-4 py-3 text-primary-foreground">
            <p className="text-sm whitespace-pre-wrap">{message.content}</p>
          </div>
        ) : (
          <AssistantMessage key={message.id} message={message} />
        ),
      )}
      {streaming && <StreamingMessage sources={streaming.sources} text={streaming.text} />}
      <div ref={bottomRef} />
    </div>
  );
}
