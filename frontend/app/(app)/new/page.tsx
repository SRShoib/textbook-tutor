"use client";

/**
 * What: where every "start a new chat" action lands -- "New chat" in the
 * sidebar, the root redirect, and post-register/login. Creates a session
 * with no input at all (POST /sessions resolves the book from the caller's
 * own grade, per the 2026-09-15 admin-owned-books decision) and goes
 * straight to it; only shows anything on screen when that can't happen yet.
 * Why this route is still one hop rather than folded into app/page.tsx
 * directly: app/page.tsx sits outside the (app) route group, so it renders
 * with no sidebar around it -- the "no book yet" message needs the sidebar
 * (so a student can still see/open any older chat they already have).
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { motion } from "framer-motion";
import { BookOpen } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { apiFetch, ApiError } from "@/lib/api";
import type { SessionRead } from "@/lib/types";
import { fadeRise } from "@/lib/motion";

type State = "creating" | "no_book" | "not_ready" | "error";

const STATE_COPY: Record<Exclude<State, "creating">, { title: string; description: string }> = {
  no_book: {
    title: "No book yet",
    description: "Your class doesn't have a book yet. Ask your teacher, or check back soon!",
  },
  not_ready: {
    title: "Almost ready",
    description: "Your book is still getting ready. Please try again in a little while!",
  },
  error: {
    title: "Something went wrong",
    description: "We couldn't start a new chat. Please try again.",
  },
};

export default function NewSessionPage() {
  const router = useRouter();
  const [state, setState] = useState<State>("creating");
  // Guards only the automatic mount-time call, not the "Try again" button --
  // React 18 Strict Mode double-invokes effects in dev, and this effect has
  // no cleanup to cancel the first call's in-flight POST, so without this a
  // single page visit created two session rows (verified live: one visible,
  // one silent orphan, timestamps a fraction of a millisecond apart). Same
  // class of problem lib/api.ts's refreshSession() already guards against,
  // just page-local instead of module-shared since there's one caller here.
  const startedRef = useRef(false);

  const createSession = useCallback(async () => {
    setState("creating");
    try {
      const session = await apiFetch<SessionRead>("/sessions", { method: "POST" });
      router.replace(`/chat/${session.id}`);
    } catch (err) {
      if (err instanceof ApiError && err.code === "NO_BOOK_FOR_GRADE") setState("no_book");
      else if (err instanceof ApiError && err.code === "BOOK_NOT_READY") setState("not_ready");
      else setState("error");
    }
  }, [router]);

  useEffect(() => {
    if (startedRef.current) return;
    startedRef.current = true;
    createSession();
  }, [createSession]);

  return (
    <div className="flex flex-1 items-center justify-center bg-gradient-to-br from-indigo-50 via-background to-amber-50 p-6 dark:from-indigo-950/30 dark:via-background dark:to-amber-950/10">
      <motion.div initial="hidden" animate="visible" variants={fadeRise} className="w-full max-w-xl">
        <Card className="[--card-spacing:--spacing(6)] shadow-lg">
          <CardHeader>
            <div className="mb-2 flex size-14 items-center justify-center rounded-xl bg-primary text-primary-foreground">
              <BookOpen className="size-7" />
            </div>
            {state === "creating" ? (
              <>
                <CardTitle className="text-2xl">Setting up your chat…</CardTitle>
                <CardDescription className="text-base">This only takes a moment.</CardDescription>
              </>
            ) : (
              <>
                <CardTitle className="text-2xl">{STATE_COPY[state].title}</CardTitle>
                <CardDescription className="text-base">{STATE_COPY[state].description}</CardDescription>
              </>
            )}
          </CardHeader>
          {state !== "creating" && (
            <CardContent>
              <Button onClick={createSession} size="lg" className="w-full text-base">
                Try again
              </Button>
            </CardContent>
          )}
        </Card>
      </motion.div>
    </div>
  );
}
