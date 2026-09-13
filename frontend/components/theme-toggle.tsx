"use client";

/**
 * Light/dark toggle -- next-themes was already an installed dependency
 * (pulled in by the sonner toast component, module 1) with a mounted
 * ThemeProvider but no way for a user to actually switch themes until now.
 */

import { useSyncExternalStore } from "react";
import { useTheme } from "next-themes";
import { Moon, Sun } from "lucide-react";
import { Button } from "@/components/ui/button";

const emptySubscribe = () => () => {};

export function ThemeToggle() {
  const { resolvedTheme, setTheme } = useTheme();
  // Checking resolvedTheme === undefined alone isn't reliable -- it only
  // avoids a hydration mismatch when this component happens to first mount
  // after some other client-only gate already ran (as in the sidebar, behind
  // AppLayout's auth-loading check). Rendered straight from a real server
  // page (the auth layout, no such gate), resolvedTheme can already be
  // resolved on the client's very first hydration pass while the server
  // rendered the undefined-branch placeholder -- a genuine, reproducible
  // "Hydration failed" error, caught live via the dev server's own logs
  // when this was first added here. useSyncExternalStore's getServerSnapshot
  // is the actual sanctioned way to render one thing on the server/first
  // hydration pass and switch after -- React treats that swap as expected,
  // not a mismatch -- and unlike a mounted flag set from useEffect, there's
  // no setState-in-effect for the lint rule to flag either.
  const mounted = useSyncExternalStore(emptySubscribe, () => true, () => false);
  if (!mounted) return <div className="size-8" />;

  const isDark = resolvedTheme === "dark";

  return (
    <Button
      variant="ghost"
      size="icon"
      aria-label={isDark ? "Switch to light theme" : "Switch to dark theme"}
      onClick={() => setTheme(isDark ? "light" : "dark")}
    >
      {isDark ? <Sun className="size-4" /> : <Moon className="size-4" />}
    </Button>
  );
}
