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
    <div className="max-w-lg rounded-2xl bg-card px-4 py-3 ring-1 ring-foreground/10">
      {text ? (
        <p className="text-sm whitespace-pre-wrap">
          {text}
          <span className="ml-0.5 inline-block h-3.5 w-1.5 animate-pulse bg-foreground/50 align-middle" />
        </p>
      ) : (
        <p className="text-sm text-muted-foreground">
          {sources ? "Writing an answer…" : "Reading your book…"}
        </p>
      )}
      {sources && <SourceCard sources={sources} />}
    </div>
  );
}
