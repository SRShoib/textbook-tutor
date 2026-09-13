"use client";

import { useState, type KeyboardEvent } from "react";
import { Send } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

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
    <div className="flex gap-2 border-t p-3">
      <Input
        value={value}
        onChange={(event) => setValue(event.target.value)}
        onKeyDown={handleKeyDown}
        disabled={disabled}
        placeholder="Ask a question about your book…"
        className="flex-1"
      />
      <Button onClick={submit} disabled={disabled || !value.trim()} aria-label="Send">
        <Send className="size-4" />
      </Button>
    </div>
  );
}
