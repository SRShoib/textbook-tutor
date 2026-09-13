/**
 * Small display-formatting helpers. Hand-rolled rather than a date library
 * dependency (date-fns etc.) for one relative-timestamp string -- same
 * "no dependency to defend for no gain" call as module 1's form-library
 * decision.
 */

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;
const WEEK = 7 * DAY;

export function relativeTime(iso: string): string {
  const then = new Date(iso).getTime();
  const diff = Date.now() - then;

  if (diff < MINUTE) return "just now";
  if (diff < HOUR) return `${Math.floor(diff / MINUTE)}m ago`;
  if (diff < DAY) return `${Math.floor(diff / HOUR)}h ago`;
  if (diff < WEEK) return `${Math.floor(diff / DAY)}d ago`;

  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}
