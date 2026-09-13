import { Bot } from "lucide-react";
import type { MessageRead } from "@/lib/types";
import { EvidencePanel } from "./evidence-panel";
import { SourceCard } from "./source-card";
import { HIDDEN_TEXT_MESSAGE, STATUS_META, isTextHiddenStatus } from "./status-meta";

/** A settled assistant turn -- same shape whether it just streamed in
 * (module 5's `done` event) or was loaded from GET /sessions/{id}/messages,
 * so the two never render differently. Max-width is controlled by the
 * parent (message-list.tsx's centered column), not here. */
export function AssistantMessage({ message }: { message: MessageRead }) {
  const status = message.status ?? "answered";
  const meta = STATUS_META[status];
  const Icon = meta.icon;
  const displayText = isTextHiddenStatus(status) ? HIDDEN_TEXT_MESSAGE : message.content;

  return (
    <div className="flex items-start gap-3">
      <div className="flex size-8 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground">
        <Bot className="size-4" />
      </div>
      <div className={`min-w-0 rounded-2xl px-5 py-3.5 ${meta.bubbleClassName}`}>
        <div className={`mb-1.5 flex items-center gap-1.5 text-sm font-medium ${meta.iconClassName}`}>
          <Icon className="size-4" />
          {meta.label}
        </div>
        <p className="font-reading text-lg leading-relaxed whitespace-pre-wrap">{displayText}</p>
        {message.sources && <SourceCard sources={message.sources} />}
        <EvidencePanel
          status={status}
          content={message.content}
          verification={message.verification}
          readability={message.readability}
        />
      </div>
    </div>
  );
}
