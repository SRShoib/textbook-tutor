"use client";

/**
 * What: the admin dashboard -- upload a textbook once (permanent, shared by
 * every student in that class) and see/delete every book that exists.
 * Why this duplicates upload/page.tsx's upload-and-poll logic instead of
 * sharing it: that page is student-facing and is deleted outright in module
 * 4 (students stop uploading entirely) -- extracting a shared hook now would
 * be refactoring code that's about to have only one caller anyway.
 * Visual pass: KPI row + two-column layout + drag-and-drop upload, done
 * within the app's existing Indigo/Amber tokens (globals.css) rather than a
 * new palette -- status colors (ready/processing/failed) stay the same
 * emerald/amber/destructive set chat/status-meta.ts already established,
 * per the dataviz skill's "status colors are reserved, never reused as a
 * categorical series" rule, and always ship with an icon + label, never
 * color alone.
 */

import { useEffect, useRef, useState, type DragEvent, type FormEvent } from "react";
import { AnimatePresence, motion } from "framer-motion";
import {
  AlertTriangle,
  BookOpen,
  CheckCircle2,
  FileText,
  Loader2,
  Trash2,
  UploadCloud,
  type LucideIcon,
} from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { apiFetch, apiUpload, ApiError } from "@/lib/api";
import { relativeTime } from "@/lib/format";
import { fadeRise, staggerContainer, staggerItem } from "@/lib/motion";
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

function StatTile({
  icon: Icon,
  label,
  value,
  tone,
}: {
  icon: LucideIcon;
  label: string;
  value: number;
  tone: "primary" | "good" | "attention" | "neutral";
}) {
  const toneClass = {
    primary: "bg-primary/10 text-primary",
    good: "bg-emerald-50 text-emerald-600 dark:bg-emerald-950/40 dark:text-emerald-400",
    attention: "bg-amber-50 text-amber-600 dark:bg-amber-950/40 dark:text-amber-400",
    neutral: "bg-muted text-muted-foreground",
  }[tone];

  return (
    <motion.div
      variants={staggerItem}
      className="flex items-center gap-5 rounded-xl bg-card p-7 ring-1 ring-foreground/10 shadow-sm transition-shadow hover:shadow-md"
    >
      <div className={`flex size-14 shrink-0 items-center justify-center rounded-xl ${toneClass}`}>
        <Icon className="size-6" />
      </div>
      <div className="flex flex-col">
        <span className="text-3xl font-semibold leading-none tabular-nums">{value}</span>
        <span className="mt-1 text-sm text-muted-foreground">{label}</span>
      </div>
    </motion.div>
  );
}

export default function AdminPage() {
  const [books, setBooks] = useState<BookRead[]>([]);
  const [loading, setLoading] = useState(true);
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [titleTouched, setTitleTouched] = useState(false);
  const [grade, setGrade] = useState(5);
  const [uploading, setUploading] = useState(false);
  const [isDragging, setIsDragging] = useState(false);

  const pollTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

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

  function applyFile(chosen: File | null) {
    setFile(chosen);
    if (chosen && !titleTouched) {
      const derived = chosen.name.replace(/\.pdf$/i, "").replace(/[_-]+/g, " ").trim();
      setTitle(derived);
    }
  }

  function handleFileChange(event: React.ChangeEvent<HTMLInputElement>) {
    applyFile(event.target.files?.[0] ?? null);
  }

  function handleDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setIsDragging(false);
    applyFile(event.dataTransfer.files?.[0] ?? null);
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

  const readyCount = books.filter((b) => b.status === "ready").length;
  const attentionCount = books.length - readyCount;

  return (
    <div className="relative flex-1 overflow-y-auto bg-gradient-to-br from-indigo-50/50 via-background to-amber-50/30 p-6 dark:from-indigo-950/10 dark:via-background dark:to-amber-950/5 lg:p-10 xl:p-14">
      <motion.div
        initial="hidden"
        animate="visible"
        variants={fadeRise}
        className="mx-auto flex w-full max-w-[1800px] flex-col gap-8"
      >
        <div>
          <p className="text-xs font-semibold tracking-wider text-primary uppercase">Admin</p>
          <h1 className="font-heading text-4xl font-semibold tracking-tight">Manage textbooks</h1>
          <p className="mt-1.5 text-base text-muted-foreground">
            Upload a book once — every student in that class uses it automatically.
          </p>
        </div>

        <motion.div
          initial="hidden"
          animate="visible"
          variants={staggerContainer}
          className="grid grid-cols-1 gap-5 sm:grid-cols-3"
        >
          <StatTile icon={BookOpen} label="Textbooks" value={books.length} tone="primary" />
          <StatTile icon={CheckCircle2} label="Ready" value={readyCount} tone="good" />
          <StatTile
            icon={AlertTriangle}
            label="Needs attention"
            value={attentionCount}
            tone={attentionCount > 0 ? "attention" : "neutral"}
          />
        </motion.div>

        <div className="grid gap-8 lg:grid-cols-[440px_1fr]">
          <Card className="[--card-spacing:--spacing(6)] lg:self-start">
            <CardHeader>
              <CardTitle className="text-lg">Add a textbook</CardTitle>
              <CardDescription>The newest ready book for a class is the one students get.</CardDescription>
            </CardHeader>
            <CardContent>
              <form onSubmit={handleSubmit} className="flex flex-col gap-4">
                <div className="flex flex-col gap-2">
                  <Label htmlFor="file">Textbook PDF</Label>
                  <div
                    role="button"
                    tabIndex={0}
                    onClick={() => fileInputRef.current?.click()}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" || event.key === " ") fileInputRef.current?.click();
                    }}
                    onDragOver={(event) => {
                      event.preventDefault();
                      setIsDragging(true);
                    }}
                    onDragLeave={() => setIsDragging(false)}
                    onDrop={handleDrop}
                    className={`flex cursor-pointer flex-col items-center gap-3 rounded-xl border-2 border-dashed p-8 text-center transition-colors ${
                      isDragging ? "border-primary bg-primary/5" : "border-border hover:border-primary/50 hover:bg-muted/50"
                    }`}
                  >
                    <div
                      className={`flex size-12 items-center justify-center rounded-full ${
                        file ? "bg-primary/10 text-primary" : "bg-muted text-muted-foreground"
                      }`}
                    >
                      {file ? <FileText className="size-6" /> : <UploadCloud className="size-6" />}
                    </div>
                    {file ? (
                      <div className="flex flex-col items-center">
                        <span className="max-w-full truncate text-sm font-medium">{file.name}</span>
                        <span className="text-xs text-muted-foreground">{(file.size / 1024 / 1024).toFixed(1)} MB</span>
                      </div>
                    ) : (
                      <div className="flex flex-col items-center">
                        <span className="text-sm font-medium">Drop a PDF here, or click to browse</span>
                        <span className="text-xs text-muted-foreground">PDF only</span>
                      </div>
                    )}
                    <input
                      ref={fileInputRef}
                      id="file"
                      type="file"
                      accept="application/pdf"
                      onChange={handleFileChange}
                      className="sr-only"
                    />
                  </div>
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
                <Button type="submit" disabled={!file || uploading} size="lg" className="w-full gap-2">
                  {uploading ? <Loader2 className="size-4 animate-spin" /> : <UploadCloud className="size-4" />}
                  {uploading ? "Uploading…" : "Upload"}
                </Button>
              </form>
            </CardContent>
          </Card>

          <Card className="[--card-spacing:--spacing(6)]">
            <CardHeader>
              <CardTitle className="text-lg">Textbooks</CardTitle>
              <CardDescription>
                {books.length} book{books.length === 1 ? "" : "s"}
              </CardDescription>
            </CardHeader>
            <CardContent>
              {loading ? (
                <div className="flex items-center gap-2 py-6 text-sm text-muted-foreground">
                  <Loader2 className="size-4 animate-spin" />
                  Loading…
                </div>
              ) : books.length === 0 ? (
                <div className="flex flex-col items-center gap-2 py-16 text-center">
                  <div className="flex size-12 items-center justify-center rounded-full bg-muted text-muted-foreground">
                    <BookOpen className="size-5" />
                  </div>
                  <p className="text-sm text-muted-foreground">No textbooks yet — add one on the left.</p>
                </div>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-base">
                    <thead>
                      <tr className="border-b border-border/60 text-left text-xs tracking-wide text-muted-foreground uppercase">
                        <th className="py-3 pr-3 font-medium">Title</th>
                        <th className="py-3 pr-3 font-medium whitespace-nowrap">Class</th>
                        <th className="py-3 pr-3 font-medium whitespace-nowrap">Status</th>
                        <th className="py-3 pr-3 font-medium whitespace-nowrap">Chunks</th>
                        <th className="py-3 pr-3 font-medium whitespace-nowrap">Added</th>
                        <th className="py-3 font-medium" />
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-border/60">
                      <AnimatePresence initial={false}>
                        {books.map((book) => {
                          const meta = BOOK_STATUS_META[book.status];
                          return (
                            <motion.tr
                              key={book.id}
                              initial={{ opacity: 0 }}
                              animate={{ opacity: 1 }}
                              exit={{ opacity: 0 }}
                              transition={{ duration: 0.15 }}
                              className="group transition-colors hover:bg-muted/40"
                            >
                              <td className="py-3.5 pr-3 font-medium">{book.title}</td>
                              <td className="py-3.5 pr-3 whitespace-nowrap">
                                <span className="inline-flex items-center rounded-md bg-muted px-2 py-0.5 text-xs font-medium">
                                  Class {book.grade}
                                </span>
                              </td>
                              <td className="py-3.5 pr-3 whitespace-nowrap">
                                <span
                                  className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${meta.className}`}
                                >
                                  {book.status === "processing" && <Loader2 className="mr-1 size-3 animate-spin" />}
                                  {meta.label}
                                </span>
                              </td>
                              <td className="py-3.5 pr-3 tabular-nums text-muted-foreground whitespace-nowrap">{book.chunk_count}</td>
                              <td className="py-3.5 pr-3 text-muted-foreground whitespace-nowrap">{relativeTime(book.created_at)}</td>
                              <td className="py-3.5 text-right whitespace-nowrap">
                                <Button
                                  size="icon"
                                  variant="ghost"
                                  aria-label="Delete book"
                                  onClick={() => handleDelete(book)}
                                  className="opacity-0 transition-opacity group-hover:opacity-100 focus-visible:opacity-100"
                                >
                                  <Trash2 className="size-4" />
                                </Button>
                              </td>
                            </motion.tr>
                          );
                        })}
                      </AnimatePresence>
                    </tbody>
                  </table>
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      </motion.div>
    </div>
  );
}
