"use client";

/**
 * Placeholder only -- real chat (streaming, source cards, status designs,
 * evidence panel) is Phase 7 module 6, gated on module 5's SSE endpoint.
 * This exists to prove upload -> session -> landing page actually connects:
 * it fetches the real session (GET /sessions/{id}, module 2) instead of
 * showing a canned string, unlike module 1's static /chat placeholder it
 * replaces.
 */

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/lib/auth-context";
import { apiFetch, ApiError } from "@/lib/api";
import type { SessionRead } from "@/lib/types";

export default function ChatPlaceholderPage() {
  const params = useParams<{ sessionId: string }>();
  const router = useRouter();
  const { user, logout } = useAuth();
  const [session, setSession] = useState<SessionRead | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    apiFetch<SessionRead>(`/sessions/${params.sessionId}`)
      .then((result) => {
        if (!cancelled) setSession(result);
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof ApiError ? err.message : "Could not load this session.");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [params.sessionId]);

  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-4 p-6 text-center">
      {error ? (
        <>
          <p className="text-sm text-destructive">{error}</p>
          <Button variant="outline" onClick={() => router.push("/upload")}>
            Add a textbook
          </Button>
        </>
      ) : session ? (
        <div>
          <p className="text-lg font-medium">{session.title ?? "New conversation"}</p>
          <p className="text-sm text-muted-foreground">Class {session.grade}</p>
        </div>
      ) : (
        <p className="text-sm text-muted-foreground">Loading…</p>
      )}
      <p className="max-w-sm text-sm text-muted-foreground">
        Signed in as {user?.display_name}. The real chat interface (streaming answers, sources, and
        verification) is built in a later Phase 7 module.
      </p>
      <Button variant="outline" onClick={() => logout()}>
        Log out
      </Button>
    </div>
  );
}
