import { BookOpen } from "lucide-react";

/** Small reusable wordmark -- no image asset pipeline needed, just an icon
 * badge + text. Used on auth pages and the sidebar. */
export function Logo({ className }: { className?: string }) {
  return (
    <div className={`flex items-center gap-2.5 ${className ?? ""}`}>
      <div className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-primary text-primary-foreground">
        <BookOpen className="size-5" />
      </div>
      <span className="font-heading text-xl font-semibold tracking-tight">Textbook Tutor</span>
    </div>
  );
}
