"use client";

/**
 * What: the admin dashboard -- upload a textbook once (permanent, shared by
 * every student in that class) and see/delete every book that exists.
 * Why this duplicates upload/page.tsx's upload-and-poll logic instead of
 * sharing it: that page is student-facing and is deleted outright in module
 * 4 (students stop uploading entirely) -- extracting a shared hook now would
 * be refactoring code that's about to have only one caller anyway.
 */

import { useEffect, useRef, useState, type FormEvent } from "react";
import { motion } from "framer-motion";
import { Loader2, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { apiFetch, apiUpload, ApiError } from "@/lib/api";
import { relativeTime } from "@/lib/format";
import { fadeRise } from "@/lib/motion";
import type { BookRead, BookStatus } from "@/lib/types";

const GRADES = Array.from({ length: 12 }, (_, i) => i + 1);
const POLL_INTERVAL_MS = 2000;

// Local to this file on purpose -- chat/status-meta.ts's STATUS_META is keyed
// on messages.status and used by several chat components; nothing else needs
// a books.status -> label/color mapping, so a shared module would be one
// more file to open for a single three-row lookup.
const BOOK_STATUS_META: Record<BookStatus, { label: string; className: string }> = {
  processing: {
    label: "Processing",
    className: "bg-amber-50 text-amber-700 ring-1 ring-amber-200 dark:bg-amber-950/40 dark:text-amber-400 dark:ring-amber-800",
  },
  ready: {
    label: "Ready",
    className: "bg-emerald-50 text-emerald-700 ring-1 ring-emerald-200 dark:bg-emerald-950/40 dark:text-emerald-400 dark:ring-emerald-800",
  },
  failed: {
    label: "Failed",
    className: "bg-destructive/10 text-destructive ring-1 ring-destructive/30",
  },
};

const ERROR_MESSAGES: Record<string, string> = {
  UNSUPPORTED_FILE_TYPE: "Please choose a PDF file.",
};

export default function AdminPage() {
  const [books, setBooks] = useState<BookRead[]>([]);
  const [loading, setLoading] = useState(true);
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [titleTouched, setTitleTouched] = useState(false);
  const [grade, setGrade] = useState(5);
  const [uploading, setUploading] = useState(false);

  const pollTimer = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    loadBooks();
    return () => {
      if (pollTimer.current) clearInterval(pollTimer.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function loadBooks() {
    try {
      const result = await apiFetch<BookRead[]>("/books");
      setBooks(result);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Could not load books.");
    } finally {
      setLoading(false);
    }
  }

  function upsertBook(book: BookRead) {
    setBooks((prev) => [book, ...prev.filter((b) => b.id !== book.id)]);
  }

  function resetForm() {
    setFile(null);
    setTitle("");
    setTitleTouched(false);
    setGrade(5);
  }

  function handleFileChange(event: React.ChangeEvent<HTMLInputElement>) {
    const chosen = event.target.files?.[0] ?? null;
    setFile(chosen);
    if (chosen && !titleTouched) {
      const derived = chosen.name.replace(/\.pdf$/i, "").replace(/[_-]+/g, " ").trim();
      setTitle(derived);
    }
  }

  function pollUntilSettled(id: string) {
    pollTimer.current = setInterval(async () => {
      try {
        const book = await apiFetch<BookRead>(`/books/${id}`);
        if (book.status === "ready") {
          if (pollTimer.current) clearInterval(pollTimer.current);
          upsertBook(book);
          setUploading(false);
          resetForm();
          toast.success("Book added.");
        } else if (book.status === "failed") {
          if (pollTimer.current) clearInterval(pollTimer.current);
          upsertBook(book);
          setUploading(false);
          toast.error("Something went wrong processing this book.");
        }
      } catch {
        // A transient network hiccup mid-poll shouldn't kill the whole flow --
        // just skip this tick and try again on the next interval.
      }
    }, POLL_INTERVAL_MS);
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (!file) return;
    setUploading(true);

    const formData = new FormData();
    formData.append("file", file);
    formData.append("title", title || file.name);
    formData.append("grade", String(grade));

    try {
      const book = await apiUpload<BookRead>("/books", formData);
      upsertBook(book);
      if (book.status === "ready") {
        setUploading(false);
        resetForm();
        toast.success("Book added.");
      } else if (book.status === "failed") {
        setUploading(false);
        toast.error("Something went wrong processing this book.");
      } else {
        pollUntilSettled(book.id);
      }
    } catch (err) {
      setUploading(false);
      toast.error(
        err instanceof ApiError ? (ERROR_MESSAGES[err.code] ?? err.message) : "Something went wrong. Please try again.",
      );
    }
  }

  async function handleDelete(book: BookRead) {
    if (!window.confirm(`Delete "${book.title}"? This can't be undone.`)) return;
    try {
      await apiFetch<void>(`/books/${book.id}`, { method: "DELETE" });
      setBooks((prev) => prev.filter((b) => b.id !== book.id));
      toast.success("Book deleted.");
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Could not delete this book.");
    }
  }

  return (
    <div className="flex-1 overflow-y-auto p-6">
      <motion.div initial="hidden" animate="visible" variants={fadeRise} className="mx-auto flex max-w-3xl flex-col gap-6">
        <div>
          <h1 className="text-2xl font-semibold">Manage textbooks</h1>
          <p className="text-muted-foreground">
            Upload a book once — every student in that class uses it automatically.
          </p>
        </div>

        <Card>
          <CardHeader>
            <CardTitle>Add a textbook</CardTitle>
            <CardDescription>The newest ready book for a class is the one students get.</CardDescription>
          </CardHeader>
          <CardContent>
            <form onSubmit={handleSubmit} className="flex flex-col gap-4">
              <div className="flex flex-col gap-2">
                <Label htmlFor="file">Textbook PDF</Label>
                <input
                  id="file"
                  type="file"
                  accept="application/pdf"
                  required
                  onChange={handleFileChange}
                  className="text-sm file:mr-3 file:rounded-lg file:border-0 file:bg-secondary file:px-3 file:py-1.5 file:text-sm file:font-medium"
                />
              </div>
              <div className="flex flex-col gap-2">
                <Label htmlFor="title">Title</Label>
                <Input
                  id="title"
                  required
                  value={title}
                  onChange={(event) => {
                    setTitleTouched(true);
                    setTitle(event.target.value);
                  }}
                />
              </div>
              <div className="flex flex-col gap-2">
                <Label htmlFor="grade">Class</Label>
                <select
                  id="grade"
                  required
                  value={grade}
                  onChange={(event) => setGrade(Number(event.target.value))}
                  className="border-input h-8 rounded-lg border bg-transparent px-2.5 text-sm shadow-xs outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
                >
                  {GRADES.map((g) => (
                    <option key={g} value={g}>
                      Class {g}
                    </option>
                  ))}
                </select>
              </div>
              <Button type="submit" disabled={!file || uploading} className="w-fit">
                {uploading ? "Uploading…" : "Upload"}
              </Button>
            </form>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Textbooks</CardTitle>
            <CardDescription>
              {books.length} book{books.length === 1 ? "" : "s"}
            </CardDescription>
          </CardHeader>
          <CardContent>
            {loading ? (
              <p className="text-sm text-muted-foreground">Loading…</p>
            ) : books.length === 0 ? (
              <p className="text-sm text-muted-foreground">No textbooks yet — add one above.</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b text-left text-muted-foreground">
                      <th className="py-2 pr-3 font-medium">Title</th>
                      <th className="py-2 pr-3 font-medium">Class</th>
                      <th className="py-2 pr-3 font-medium">Status</th>
                      <th className="py-2 pr-3 font-medium">Chunks</th>
                      <th className="py-2 pr-3 font-medium">Added</th>
                      <th className="py-2 font-medium" />
                    </tr>
                  </thead>
                  <tbody>
                    {books.map((book) => {
                      const meta = BOOK_STATUS_META[book.status];
                      return (
                        <tr key={book.id} className="border-b last:border-0">
                          <td className="py-2 pr-3">{book.title}</td>
                          <td className="py-2 pr-3">Class {book.grade}</td>
                          <td className="py-2 pr-3">
                            <span
                              className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${meta.className}`}
                            >
                              {book.status === "processing" && <Loader2 className="mr-1 size-3 animate-spin" />}
                              {meta.label}
                            </span>
                          </td>
                          <td className="py-2 pr-3">{book.chunk_count}</td>
                          <td className="py-2 pr-3 text-muted-foreground">{relativeTime(book.created_at)}</td>
                          <td className="py-2 text-right">
                            <Button size="icon" variant="ghost" aria-label="Delete book" onClick={() => handleDelete(book)}>
                              <Trash2 className="size-4" />
                            </Button>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </CardContent>
        </Card>
      </motion.div>
    </div>
  );
}
