"use client";

/**
 * "How I checked this" -- collapsed by default, the live demo of research
 * contributions 1 (grounding) and 3 (NLI verification) at the defense.
 * Plain useState, not a new Collapsible dependency; content fades in via
 * lib/motion.ts's existing `fade` variant (opacity only, no height
 * animation) rather than animating a layout property.
 */

import { useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { ChevronDown } from "lucide-react";
import type { MessageStatus, StyleReport, VerificationReport } from "@/lib/types";
import { fade } from "@/lib/motion";
import { isTextHiddenStatus } from "./status-meta";

export function EvidencePanel({
  status,
  content,
  verification,
  readability,
}: {
  status: MessageStatus;
  content: string;
  verification: VerificationReport | null;
  readability: StyleReport | null;
}) {
  const [open, setOpen] = useState(false);

  if (verification === null && readability === null && !isTextHiddenStatus(status)) {
    return null; // nothing to show (e.g. a plain off-book refusal)
  }

  return (
    <div className="mt-2">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1 rounded-full px-2.5 py-1.5 text-sm text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
      >
        <ChevronDown className={`size-4 transition-transform ${open ? "rotate-180" : ""}`} />
        How I checked this
      </button>
      <AnimatePresence>
        {open && (
          <motion.div
            initial="hidden"
            animate="visible"
            exit="exit"
            variants={fade}
            className="mt-2 flex flex-col gap-4 rounded-xl border border-border/60 bg-card/80 p-4 text-sm"
          >
            {isTextHiddenStatus(status) && (
              <div>
                <p className="font-medium">Generated but not shown to the student:</p>
                <p className="mt-1 text-muted-foreground">{content}</p>
              </div>
            )}

            {verification && (
              <div>
                <p className="font-medium">
                  Book support: {verification.sentences.filter((s) => s.supported).length}/
                  {verification.sentences.length} sentences (
                  {Math.round(verification.supported_ratio * 100)}%)
                </p>
                <ul className="mt-1 flex flex-col gap-1">
                  {verification.sentences.map((s, i) => (
                    <li key={i} className="flex items-start gap-1.5">
                      <span
                        className={`mt-0.5 flex size-4 shrink-0 items-center justify-center rounded-full text-[10px] font-bold ${
                          s.supported
                            ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-900 dark:text-emerald-300"
                            : "bg-destructive/15 text-destructive"
                        }`}
                      >
                        {s.supported ? "✓" : "✗"}
                      </span>
                      <span className={s.supported ? "" : "text-destructive"}>
                        {s.sentence}{" "}
                        <span className="text-muted-foreground">({s.entailment_score.toFixed(2)})</span>
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {readability && (
              <div>
                <p className="font-medium">Readability: {readability.passed ? "passed" : "did not pass"}</p>
                <p className="mt-1 text-muted-foreground">
                  FK grade {readability.fk_grade?.toFixed(2) ?? "n/a"} · vocab coverage{" "}
                  {Math.round(readability.vocab_coverage * 100)}% · longest sentence{" "}
                  {readability.max_sentence_words} words
                </p>
              </div>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
