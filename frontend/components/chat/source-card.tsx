import { BookOpen } from "lucide-react";
import type { SourceCitation } from "@/lib/types";

/** Lists cited lessons. Rendered even for refused_off_book -- graph.py's
 * build_sources() still cites what was retrieved on a refusal (it just
 * scored below the gate), and showing "here's what I looked at" is honest,
 * not a display bug. */
export function SourceCard({ sources }: { sources: SourceCitation[] }) {
  if (sources.length === 0) return null;

  return (
    <div className="mt-2 flex flex-col gap-1 rounded-lg bg-muted/50 p-2 text-xs text-muted-foreground">
      <div className="flex items-center gap-1 font-medium">
        <BookOpen className="size-3.5" />
        Referenced lessons
      </div>
      {sources.map((s) => (
        <div key={s.lesson_id}>
          Unit {s.unit}, Lesson {s.lesson_no}: {s.lesson_title} (page {s.page})
        </div>
      ))}
    </div>
  );
}
