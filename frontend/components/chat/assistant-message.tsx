import type { MessageRead } from "@/lib/types";
import { EvidencePanel } from "./evidence-panel";
import { SourceCard } from "./source-card";
import { HIDDEN_TEXT_MESSAGE, STATUS_META, isTextHiddenStatus } from "./status-meta";

/** A settled assistant turn -- same shape whether it just streamed in
 * (module 5's `done` event) or was loaded from GET /sessions/{id}/messages,
 * so the two never render differently. */
export function AssistantMessage({ message }: { message: MessageRead }) {
  const status = message.status ?? "answered";
  const meta = STATUS_META[status];
  const Icon = meta.icon;
  const displayText = isTextHiddenStatus(status) ? HIDDEN_TEXT_MESSAGE : message.content;

  return (
    <div className={`max-w-lg rounded-2xl px-4 py-3 ${meta.bubbleClassName}`}>
      <div className={`mb-1 flex items-center gap-1 text-xs font-medium ${meta.iconClassName}`}>
        <Icon className="size-3.5" />
        {meta.label}
      </div>
      <p className="text-sm whitespace-pre-wrap">{displayText}</p>
      {message.sources && <SourceCard sources={message.sources} />}
      <EvidencePanel
        status={status}
        content={message.content}
        verification={message.verification}
        readability={message.readability}
      />
    </div>
  );
}
