"use client";

/**
 * Light/dark toggle -- next-themes was already an installed dependency
 * (pulled in by the sonner toast component, module 1) with a mounted
 * ThemeProvider but no way for a user to actually switch themes until now.
 */

import { useTheme } from "next-themes";
import { Moon, Sun } from "lucide-react";
import { Button } from "@/components/ui/button";

export function ThemeToggle() {
  const { resolvedTheme, setTheme } = useTheme();
  // resolvedTheme is undefined until next-themes' own effect resolves the
  // OS/stored preference on the client -- checking it directly (rather than
  // a separate "mounted" state set from our own effect) avoids a hydration
  // mismatch without a synchronous setState-in-effect of our own.
  if (resolvedTheme === undefined) return <div className="size-8" />;

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
