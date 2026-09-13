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

// A snappier "ease-out-expo"-ish curve instead of the generic easeOut/easeIn
// strings -- decelerates hard at the very end, which reads as more
// deliberate/premium than a linear-ish standard ease.
const EASE_OUT = [0.16, 1, 0.3, 1] as const;
const EASE_IN = [0.7, 0, 0.84, 0] as const;

/** Default page/card entrance: fade + small upward rise. */
export const fadeRise: Variants = {
  hidden: { opacity: 0, y: 8 },
  visible: {
    opacity: 1,
    y: 0,
    transition: { duration: DURATION.enter, ease: EASE_OUT },
  },
  exit: {
    opacity: 0,
    y: -4,
    transition: { duration: DURATION.exit, ease: EASE_IN },
  },
};

/** Plain fade, for content that shouldn't shift position (e.g. status swaps). */
export const fade: Variants = {
  hidden: { opacity: 0 },
  visible: { opacity: 1, transition: { duration: DURATION.enter, ease: EASE_OUT } },
  exit: { opacity: 0, transition: { duration: DURATION.exit, ease: EASE_IN } },
};

/** Wraps a list of `staggerItem` children so they enter one after another
 * instead of all at once -- e.g. a run of settled chat messages. */
export const staggerContainer: Variants = {
  hidden: {},
  visible: {
    transition: { staggerChildren: 0.06 },
  },
};

export const staggerItem: Variants = {
  hidden: { opacity: 0, y: 8 },
  visible: {
    opacity: 1,
    y: 0,
    transition: { duration: DURATION.enter, ease: EASE_OUT },
  },
};
