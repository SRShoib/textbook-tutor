import { BookOpen } from "lucide-react";
import type { SourceCitation } from "@/lib/types";

/** Lists cited lessons. Rendered even for refused_off_book -- graph.py's
 * build_sources() still cites what was retrieved on a refusal (it just
 * scored below the gate), and showing "here's what I looked at" is honest,
 * not a display bug. */
export function SourceCard({ sources }: { sources: SourceCitation[] }) {
  if (sources.length === 0) return null;

  return (
    <div className="mt-3 flex flex-col gap-2 rounded-xl border border-border/60 bg-card/80 p-3 text-sm text-muted-foreground">
      <div className="flex items-center gap-1.5 font-medium text-foreground/80">
        <BookOpen className="size-4 text-primary" />
        Referenced lessons
      </div>
      {sources.map((s) => (
        <div key={s.lesson_id} className="rounded-md px-1.5 py-0.5">
          Unit {s.unit}, Lesson {s.lesson_no}: {s.lesson_title} (page {s.page})
        </div>
      ))}
    </div>
  );
}
