"use client";

/**
 * What: the persistent session list rendered by app/(app)/layout.tsx on
 * every authenticated screen. Session list, rename, delete, and
 * active-session highlighting -- Phase 7 module 4.
 * Why it refetches on every route change (usePathname in the effect deps)
 * instead of sharing state some other way: it's one cheap GET, and it means
 * a session created from the /chat/new draft (see that page's docstring for
 * why "new" never touches the database until the first message is sent)
 * just shows up the moment its URL is promoted to a real id, with no
 * cross-page state plumbing needed.
 */

import { useEffect, useState, type KeyboardEvent } from "react";
import Link from "next/link";
import { useParams, usePathname, useRouter } from "next/navigation";
import { LogOut, Pencil, Plus, Settings, Shield, Trash2, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Logo } from "@/components/logo";
import { ThemeToggle } from "@/components/theme-toggle";
import { useAuth } from "@/lib/auth-context";
import { apiFetch, ApiError } from "@/lib/api";
import { relativeTime } from "@/lib/format";
import type { SessionRead } from "@/lib/types";

export function Sidebar({ open, onClose }: { open: boolean; onClose: () => void }) {
  const pathname = usePathname();
  const router = useRouter();
  const params = useParams<{ sessionId?: string }>();
  const { user, logout } = useAuth();

  const [sessions, setSessions] = useState<SessionRead[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadToken, setReloadToken] = useState(0);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editValue, setEditValue] = useState("");

  useEffect(() => {
    let cancelled = false;
    // No synchronous setLoading(true) here on purpose: this effect re-runs
    // on every navigation (pathname changes), and re-flashing the whole
    // sidebar to a loading state on every click would be jarring -- the
    // stale list stays visible until the fresh one arrives, loading only
    // ever goes true -> false once, on the very first mount.
    (async () => {
      try {
        const result = await apiFetch<SessionRead[]>("/sessions");
        if (cancelled) return;
        setSessions(result);
        setError(null);
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof ApiError ? err.message : "Could not load your conversations.");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    // Also closes the mobile drawer on every navigation -- onClose is
    // useCallback-stabilized by the layout, so this doesn't re-fire on
    // unrelated parent re-renders.
    onClose();
    return () => {
      cancelled = true;
    };
  }, [pathname, reloadToken, onClose]);

  useEffect(() => {
    if (!open) return;
    function handleKeyDown(event: globalThis.KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [open, onClose]);

  function startEditing(session: SessionRead) {
    setEditingId(session.id);
    setEditValue(session.title ?? "");
  }

  async function commitRename(sessionId: string) {
    const title = editValue.trim();
    setEditingId(null);
    if (!title) return;
    try {
      const updated = await apiFetch<SessionRead>(`/sessions/${sessionId}`, {
        method: "PATCH",
        body: { title },
      });
      setSessions((prev) => prev.map((s) => (s.id === sessionId ? updated : s)));
    } catch {
      // A failed rename just leaves the old title in place -- not worth a
      // toast for a low-stakes edit; the pencil is always there to retry.
    }
  }

  function handleEditKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Enter") {
      event.currentTarget.blur();
    } else if (event.key === "Escape") {
      setEditingId(null);
    }
  }

  async function handleDelete(session: SessionRead) {
    if (!window.confirm(`Delete "${session.title ?? "this conversation"}"? This can't be undone.`)) {
      return;
    }
    try {
      await apiFetch<void>(`/sessions/${session.id}`, { method: "DELETE" });
      setSessions((prev) => prev.filter((s) => s.id !== session.id));
      if (params.sessionId === session.id) {
        router.push("/chat/new");
      }
    } catch (err) {
      window.alert(err instanceof ApiError ? err.message : "Could not delete this conversation.");
    }
  }

  async function handleLogout() {
    await logout();
    router.push("/login");
  }

  return (
    <aside
      className={`fixed inset-y-0 left-0 z-40 flex w-80 shrink-0 flex-col border-r border-sidebar-border bg-sidebar text-sidebar-foreground transition-transform duration-200 md:relative md:translate-x-0 md:transition-none ${
        open ? "translate-x-0" : "-translate-x-full"
      }`}
    >
      <div className="flex items-center justify-between border-b border-sidebar-border p-4">
        <Logo />
        {/* The hamburger button that opens this drawer sits in the layout's
            header bar above -- once open, this fixed+z-40 aside visually
            covers it (fixed elements stack above normal-flow content
            regardless of DOM order), so there's otherwise no visible way to
            close the drawer without already knowing to tap the backdrop or
            press Escape. */}
        <Button variant="ghost" size="icon" aria-label="Close menu" onClick={onClose} className="md:hidden">
          <X className="size-5" />
        </Button>
      </div>

      <div className="flex flex-col gap-2 p-4">
        <Button
          render={<Link href="/chat/new" />}
          nativeButton={false}
          variant="secondary"
          size="lg"
          className="w-full justify-start gap-2 text-base"
        >
          <Plus className="size-4.5" />
          New chat
        </Button>
        {user?.role === "admin" && (
          <Button
            render={<Link href="/admin" />}
            nativeButton={false}
            variant="ghost"
            size="lg"
            className="w-full justify-start gap-2 text-base"
          >
            <Shield className="size-4.5" />
            Admin dashboard
          </Button>
        )}
      </div>

      <div className="flex-1 overflow-y-auto px-3 pb-3">
        {loading ? (
          <p className="p-3 text-base text-muted-foreground">Loading…</p>
        ) : error ? (
          <div className="flex flex-col gap-2 p-3">
            <p className="text-base text-destructive">{error}</p>
            <Button size="sm" variant="outline" onClick={() => setReloadToken((t) => t + 1)}>
              Retry
            </Button>
          </div>
        ) : sessions.length === 0 ? (
          <p className="p-3 text-base text-muted-foreground">No conversations yet.</p>
        ) : (
          <ul className="flex flex-col gap-1">
            {sessions.map((session) => {
              const active = session.id === params.sessionId;
              return (
                <li key={session.id}>
                  <div
                    className={`group flex items-center gap-2 rounded-lg border-l-2 px-3.5 py-3 transition-colors ${
                      active
                        ? "border-l-primary bg-sidebar-accent text-sidebar-accent-foreground"
                        : "border-l-transparent hover:bg-sidebar-accent/60"
                    }`}
                  >
                    {editingId === session.id ? (
                      <input
                        autoFocus
                        value={editValue}
                        onChange={(event) => setEditValue(event.target.value)}
                        onBlur={() => commitRename(session.id)}
                        onKeyDown={handleEditKeyDown}
                        className="min-w-0 flex-1 rounded border border-sidebar-ring bg-transparent px-1 text-base outline-none"
                      />
                    ) : (
                      <Link href={`/chat/${session.id}`} className="min-w-0 flex-1">
                        <p className="truncate text-base">{session.title ?? "New conversation"}</p>
                        <p className="truncate text-sm text-muted-foreground">
                          {relativeTime(session.updated_at)} · Class {session.grade}
                        </p>
                      </Link>
                    )}
                    {editingId !== session.id && (
                      <div className="hidden shrink-0 gap-1 group-hover:flex group-focus-within:flex">
                        <Button
                          size="icon"
                          variant="ghost"
                          aria-label="Rename conversation"
                          onClick={() => startEditing(session)}
                        >
                          <Pencil className="size-4" />
                        </Button>
                        <Button
                          size="icon"
                          variant="ghost"
                          aria-label="Delete conversation"
                          onClick={() => handleDelete(session)}
                        >
                          <Trash2 className="size-4" />
                        </Button>
                      </div>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </div>

      <div className="flex items-center justify-between gap-2 border-t border-sidebar-border p-4">
        <span className="truncate text-lg font-semibold" title={user?.display_name}>
          {user?.display_name}
        </span>
        <div className="flex shrink-0 items-center gap-1">
          <Button
            render={<Link href="/profile" />}
            nativeButton={false}
            variant="ghost"
            size="icon"
            aria-label="Profile settings"
          >
            <Settings className="size-4" />
          </Button>
          <ThemeToggle />
          <Button variant="ghost" size="icon" aria-label="Log out" onClick={handleLogout}>
            <LogOut className="size-4" />
          </Button>
        </div>
      </div>
    </aside>
  );
}
