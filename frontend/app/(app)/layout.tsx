"use client";

/**
 * Client-side auth guard for every screen under (app). No edge middleware:
 * the access token lives only in memory (lib/api.ts), so there is nothing
 * for middleware running outside React to inspect anyway -- see the Module 1
 * plan for why this tradeoff was chosen over duplicating JWT verification
 * in the edge runtime.
 */

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth-context";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const { status } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (status === "anon") router.replace("/login");
  }, [status, router]);

  if (status !== "authed") {
    return (
      <div className="flex flex-1 items-center justify-center">
        <p className="text-sm text-muted-foreground">Loading…</p>
      </div>
    );
  }

  return (
    <div className="flex flex-1">
      {/* Sidebar slot -- built in Phase 7 module 4, once GET /sessions exists. */}
      <div className="flex flex-1 flex-col">{children}</div>
    </div>
  );
}
