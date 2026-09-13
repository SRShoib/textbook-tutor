/**
 * One lookup from messages.status (CLAUDE.md's four fixed values) to how an
 * assistant bubble should look. No dedicated "caution" theme token exists
 * for low_confidence (currently unreachable -- nothing in the pipeline
 * emits it yet, see lib/types.ts's MessageStatus comment) so it uses plain
 * amber-* Tailwind utilities rather than inventing one; refused_unverified
 * reuses the theme's existing --destructive tokens, which already exist for
 * exactly this "something's wrong" signal.
 */

import { BookX, Check, ShieldAlert, TriangleAlert } from "lucide-react";
import type { MessageStatus } from "@/lib/types";

export interface StatusMeta {
  label: string;
  icon: typeof Check;
  bubbleClassName: string;
  iconClassName: string;
}

export const STATUS_META: Record<MessageStatus, StatusMeta> = {
  answered: {
    label: "Answered",
    icon: Check,
    bubbleClassName: "bg-emerald-50 ring-1 ring-emerald-200 dark:bg-emerald-950/40 dark:ring-emerald-800",
    iconClassName: "text-emerald-600 dark:text-emerald-400",
  },
  low_confidence: {
    label: "Not fully confident",
    icon: TriangleAlert,
    bubbleClassName: "bg-amber-50 ring-1 ring-amber-200 dark:bg-amber-950 dark:ring-amber-800",
    iconClassName: "text-amber-600 dark:text-amber-400",
  },
  refused_off_book: {
    label: "Not in this book",
    icon: BookX,
    bubbleClassName: "bg-muted ring-1 ring-foreground/10",
    iconClassName: "text-muted-foreground",
  },
  refused_unverified: {
    label: "Couldn't fully verify",
    icon: ShieldAlert,
    bubbleClassName: "bg-destructive/10 ring-1 ring-destructive/30",
    iconClassName: "text-destructive",
  },
};

/** For the two statuses whose real generated text never appears in the
 * primary bubble -- see the Module 6 plan's child-safety decision. */
export function isTextHiddenStatus(status: MessageStatus): boolean {
  return status === "refused_unverified" || status === "low_confidence";
}

export const HIDDEN_TEXT_MESSAGE = "I couldn't fully check this against your book. Let's ask your teacher about this one!";
