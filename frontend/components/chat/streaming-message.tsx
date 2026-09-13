import { Bot } from "lucide-react";
import type { SourceCitation } from "@/lib/types";
import { SourceCard } from "./source-card";

/** The in-flight assistant turn -- no status yet (that only exists once the
 * `done` event settles it into a real MessageRead, rendered by
 * AssistantMessage instead). Sources appear the moment module 5's real
 * `sources` event arrives, well before the answer itself is ready. */
export function StreamingMessage({
  sources,
  text,
}: {
  sources: SourceCitation[] | null;
  text: string;
}) {
  return (
    <div className="flex items-start gap-3">
      <div className="flex size-8 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground">
        <Bot className="size-4" />
      </div>
      <div className="min-w-0 rounded-2xl bg-card px-5 py-3.5 ring-1 ring-foreground/10">
        {text ? (
          <p className="font-reading text-lg leading-relaxed whitespace-pre-wrap">
            {text}
            <span className="ml-0.5 inline-block h-4 w-1.5 animate-pulse bg-primary/60 align-middle" />
          </p>
        ) : (
          <p className="font-reading flex items-center gap-2 text-lg text-muted-foreground">
            <span className="flex gap-0.5">
              <span className="size-1.5 animate-bounce rounded-full bg-primary/60 [animation-delay:-0.3s]" />
              <span className="size-1.5 animate-bounce rounded-full bg-primary/60 [animation-delay:-0.15s]" />
              <span className="size-1.5 animate-bounce rounded-full bg-primary/60" />
            </span>
            {sources ? "Writing an answer…" : "Reading your book…"}
          </p>
        )}
        {sources && <SourceCard sources={sources} />}
      </div>
    </div>
  );
}
