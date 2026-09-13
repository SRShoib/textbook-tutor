"use client";

/**
 * What: the persistent session list rendered by app/(app)/layout.tsx on
 * every authenticated screen. Session list, rename, delete, and
 * active-session highlighting -- Phase 7 module 4.
 * Why it refetches on every route change (usePathname in the effect deps)
 * instead of sharing state with the upload page: it's one cheap GET, and it
 * means a session created by /upload just shows up the moment you land on
 * /chat/{id}, with no cross-page state plumbing needed.
 */

import { useEffect, useState, type KeyboardEvent } from "react";
import Link from "next/link";
import { useParams, usePathname, useRouter } from "next/navigation";
import { Pencil, Plus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { apiFetch, ApiError } from "@/lib/api";
import { relativeTime } from "@/lib/format";
import type { SessionRead } from "@/lib/types";

export function Sidebar() {
  const pathname = usePathname();
  const router = useRouter();
  const params = useParams<{ sessionId?: string }>();

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
    return () => {
      cancelled = true;
    };
  }, [pathname, reloadToken]);

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
        router.push("/upload");
      }
    } catch (err) {
      window.alert(err instanceof ApiError ? err.message : "Could not delete this conversation.");
    }
  }

  return (
    <aside className="flex w-64 shrink-0 flex-col border-r border-sidebar-border bg-sidebar text-sidebar-foreground">
      <div className="p-3">
        <Button
          render={<Link href="/upload" />}
          nativeButton={false}
          variant="secondary"
          className="w-full justify-start gap-2"
        >
          <Plus className="size-4" />
          New chat
        </Button>
      </div>

      <div className="flex-1 overflow-y-auto px-2 pb-3">
        {loading ? (
          <p className="p-3 text-sm text-muted-foreground">Loading…</p>
        ) : error ? (
          <div className="flex flex-col gap-2 p-3">
            <p className="text-sm text-destructive">{error}</p>
            <Button size="sm" variant="outline" onClick={() => setReloadToken((t) => t + 1)}>
              Retry
            </Button>
          </div>
        ) : sessions.length === 0 ? (
          <p className="p-3 text-sm text-muted-foreground">No conversations yet.</p>
        ) : (
          <ul className="flex flex-col gap-0.5">
            {sessions.map((session) => {
              const active = session.id === params.sessionId;
              return (
                <li key={session.id}>
                  <div
                    className={`group flex items-center gap-1 rounded-lg px-2 py-1.5 ${
                      active ? "bg-sidebar-accent text-sidebar-accent-foreground" : "hover:bg-sidebar-accent/60"
                    }`}
                  >
                    {editingId === session.id ? (
                      <input
                        autoFocus
                        value={editValue}
                        onChange={(event) => setEditValue(event.target.value)}
                        onBlur={() => commitRename(session.id)}
                        onKeyDown={handleEditKeyDown}
                        className="min-w-0 flex-1 rounded border border-sidebar-ring bg-transparent px-1 text-sm outline-none"
                      />
                    ) : (
                      <Link href={`/chat/${session.id}`} className="min-w-0 flex-1">
                        <p className="truncate text-sm">{session.title ?? "New conversation"}</p>
                        <p className="truncate text-xs text-muted-foreground">
                          {relativeTime(session.updated_at)} · Class {session.grade}
                        </p>
                      </Link>
                    )}
                    {editingId !== session.id && (
                      <div className="hidden shrink-0 gap-0.5 group-hover:flex">
                        <Button
                          size="icon-sm"
                          variant="ghost"
                          aria-label="Rename conversation"
                          onClick={() => startEditing(session)}
                        >
                          <Pencil className="size-3.5" />
                        </Button>
                        <Button
                          size="icon-sm"
                          variant="ghost"
                          aria-label="Delete conversation"
                          onClick={() => handleDelete(session)}
                        >
                          <Trash2 className="size-3.5" />
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
    </aside>
  );
}
