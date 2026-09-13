/**
 * Shared Framer Motion variants -- transform (x/y/scale) and opacity only,
 * never layout-affecting properties, so nothing here forces a reflow.
 * Reduced motion is handled once, globally, via <MotionConfig
 * reducedMotion="user"> in app/layout.tsx (Framer Motion then renders these
 * transitions as instant end-states for anyone with the OS setting on) --
 * individual components never need to check it themselves.
 */

import type { Variants } from "framer-motion";

export const DURATION = {
  enter: 0.18,
  exit: 0.12,
} as const;

/** Default page/card entrance: fade + small upward rise. */
export const fadeRise: Variants = {
  hidden: { opacity: 0, y: 8 },
  visible: {
    opacity: 1,
    y: 0,
    transition: { duration: DURATION.enter, ease: "easeOut" },
  },
  exit: {
    opacity: 0,
    y: -4,
    transition: { duration: DURATION.exit, ease: "easeIn" },
  },
};

/** Plain fade, for content that shouldn't shift position (e.g. status swaps). */
export const fade: Variants = {
  hidden: { opacity: 0 },
  visible: { opacity: 1, transition: { duration: DURATION.enter, ease: "easeOut" } },
  exit: { opacity: 0, transition: { duration: DURATION.exit, ease: "easeIn" } },
};
