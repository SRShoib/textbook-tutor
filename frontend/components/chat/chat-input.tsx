"use client";

import { useState, type KeyboardEvent } from "react";
import { Send } from "lucide-react";
import { Button } from "@/components/ui/button";

export function ChatInput({ disabled, onSend }: { disabled: boolean; onSend: (content: string) => void }) {
  const [value, setValue] = useState("");

  function submit() {
    const trimmed = value.trim();
    if (!trimmed || disabled) return;
    onSend(trimmed);
    setValue("");
  }

  function handleKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Enter") submit();
  }

  return (
    <div className="border-t p-4">
      {/* Same max-w-3xl centered column as the header and message list. */}
      <div className="mx-auto flex w-full max-w-3xl items-center gap-2 rounded-full border border-input bg-card px-3.5 py-2.5 shadow-sm transition-colors focus-within:border-ring focus-within:ring-3 focus-within:ring-ring/50">
        <input
          value={value}
          onChange={(event) => setValue(event.target.value)}
          onKeyDown={handleKeyDown}
          disabled={disabled}
          placeholder="Ask a question about your book…"
          className="font-reading h-11 flex-1 bg-transparent px-2 text-lg outline-none placeholder:text-muted-foreground disabled:cursor-not-allowed disabled:opacity-50"
        />
        <Button
          onClick={submit}
          disabled={disabled || !value.trim()}
          aria-label="Send"
          size="icon-lg"
          className="rounded-full"
        >
          <Send className="size-4.5" />
        </Button>
      </div>
    </div>
  );
}
