"use client";

/**
 * Client-side auth guard for every screen under (app). No edge middleware:
 * the access token lives only in memory (lib/api.ts), so there is nothing
 * for middleware running outside React to inspect anyway -- see the Module 1
 * plan for why this tradeoff was chosen over duplicating JWT verification
 * in the edge runtime.
 */

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Menu } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Logo } from "@/components/logo";
import { useAuth } from "@/lib/auth-context";
import { Sidebar } from "@/components/sidebar";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const { status } = useAuth();
  const router = useRouter();
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const closeSidebar = useCallback(() => setSidebarOpen(false), []);

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
    <div className="flex h-dvh flex-col">
      <div className="flex items-center gap-2 border-b bg-card p-2 md:hidden">
        <Button variant="ghost" size="icon-sm" aria-label="Open menu" onClick={() => setSidebarOpen(true)}>
          <Menu className="size-5" />
        </Button>
        <Logo />
      </div>
      <div className="flex min-h-0 flex-1">
        {sidebarOpen && (
          <div className="fixed inset-0 z-30 bg-black/40 md:hidden" onClick={closeSidebar} aria-hidden="true" />
        )}
        <Sidebar open={sidebarOpen} onClose={closeSidebar} />
        <div className="flex min-w-0 flex-1 flex-col">{children}</div>
      </div>
    </div>
  );
}
