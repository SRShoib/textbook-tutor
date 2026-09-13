"use client";

import { useEffect, useRef } from "react";
import { motion } from "framer-motion";
import { User } from "lucide-react";
import type { MessageRead, SourceCitation } from "@/lib/types";
import { staggerContainer, staggerItem } from "@/lib/motion";
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
    <div className="flex flex-1 flex-col overflow-y-auto">
      {/* Centered reading column -- capped so it doesn't stretch bubbles
          edge-to-edge on a wide monitor, but wide enough (max-w-3xl) that
          they don't look lost in empty space either. */}
      <motion.div
        initial="hidden"
        animate="visible"
        variants={staggerContainer}
        className="mx-auto flex w-full max-w-3xl flex-1 flex-col gap-5 p-4"
      >
        {messages.map((message) =>
          message.role === "user" ? (
            <motion.div key={message.id} variants={staggerItem} className="ml-auto flex max-w-[85%] items-start gap-3">
              <div className="rounded-2xl bg-primary px-5 py-3.5 text-primary-foreground">
                <p className="font-reading text-lg leading-relaxed whitespace-pre-wrap">{message.content}</p>
              </div>
              <div className="flex size-8 shrink-0 items-center justify-center rounded-full bg-secondary text-secondary-foreground">
                <User className="size-4" />
              </div>
            </motion.div>
          ) : (
            <motion.div key={message.id} variants={staggerItem} className="max-w-[85%]">
              <AssistantMessage message={message} />
            </motion.div>
          ),
        )}
        {streaming && (
          <motion.div variants={staggerItem} className="max-w-[85%]">
            <StreamingMessage sources={streaming.sources} text={streaming.text} />
          </motion.div>
        )}
        <div ref={bottomRef} />
      </motion.div>
    </div>
  );
}
