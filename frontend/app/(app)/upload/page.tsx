"use client";

/**
 * What: the "get started" screen -- pick a class, pick a PDF, watch it
 * process, land in a session. The only way a book/session pair gets
 * created through the browser today (module 4's sidebar will add "resume
 * an existing session" on top of this).
 *
 * Why the checklist doesn't track real stages: books.status is only
 * processing/ready/failed (no per-stage signal exists on the backend, and
 * this module deliberately doesn't add one -- see the Module 3 plan). Every
 * row animates as "working" together while processing and flips to done
 * together the instant the real status changes; it never claims to know
 * which step is "really" running.
 */

import { useEffect, useRef, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { AnimatePresence, motion } from "framer-motion";
import { BookOpen, Check, Layers, Sparkles, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useAuth } from "@/lib/auth-context";
import { apiFetch, apiUpload, ApiError } from "@/lib/api";
import type { BookRead, SessionRead } from "@/lib/types";
import { fadeRise, fade, staggerContainer, staggerItem } from "@/lib/motion";

const GRADES = Array.from({ length: 12 }, (_, i) => i + 1);

// Distinct icons per conceptual step -- still all shown "in progress"
// together while processing (see the module docstring below), just visually
// richer than four identical spinners.
const CHECKLIST_STEPS = [
  { label: "Reading your book", icon: BookOpen },
  { label: "Splitting it into lessons", icon: Layers },
  { label: "Understanding the vocabulary", icon: Sparkles },
  { label: "Getting ready to answer questions", icon: Check },
] as const;

const POLL_INTERVAL_MS = 2000;
const SLOW_NOTE_AFTER_MS = 20000;

type Phase = "idle" | "submitting" | "processing" | "creating_session" | "failed" | "error";

const ERROR_MESSAGES: Record<string, string> = {
  UNSUPPORTED_FILE_TYPE: "Please choose a PDF file.",
};

export default function UploadPage() {
  const { user } = useAuth();
  const router = useRouter();

  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [titleTouched, setTitleTouched] = useState(false);
  const [grade, setGrade] = useState(user?.grade ?? 5);
  const [phase, setPhase] = useState<Phase>("idle");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [showSlowNote, setShowSlowNote] = useState(false);

  const pollTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  const slowNoteTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (pollTimer.current) clearInterval(pollTimer.current);
      if (slowNoteTimer.current) clearTimeout(slowNoteTimer.current);
    };
  }, []);

  function handleFileChange(event: React.ChangeEvent<HTMLInputElement>) {
    const chosen = event.target.files?.[0] ?? null;
    setFile(chosen);
    if (chosen && !titleTouched) {
      const derived = chosen.name.replace(/\.pdf$/i, "").replace(/[_-]+/g, " ").trim();
      setTitle(derived);
    }
  }

  async function createSessionAndRedirect(book: BookRead) {
    setPhase("creating_session");
    try {
      const session = await apiFetch<SessionRead>("/sessions", {
        method: "POST",
        body: { book_id: book.id, grade },
      });
      router.push(`/chat/${session.id}`);
    } catch (err) {
      setPhase("error");
      setErrorMessage(err instanceof ApiError ? err.message : "Could not start a session. Please try again.");
    }
  }

  function startPolling(id: string) {
    setPhase("processing");
    slowNoteTimer.current = setTimeout(() => setShowSlowNote(true), SLOW_NOTE_AFTER_MS);

    pollTimer.current = setInterval(async () => {
      try {
        const book = await apiFetch<BookRead>(`/books/${id}`);
        if (book.status === "ready") {
          if (pollTimer.current) clearInterval(pollTimer.current);
          if (slowNoteTimer.current) clearTimeout(slowNoteTimer.current);
          await createSessionAndRedirect(book);
        } else if (book.status === "failed") {
          if (pollTimer.current) clearInterval(pollTimer.current);
          if (slowNoteTimer.current) clearTimeout(slowNoteTimer.current);
          setPhase("failed");
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
    setErrorMessage(null);
    setShowSlowNote(false);
    setPhase("submitting");

    const formData = new FormData();
    formData.append("file", file);
    formData.append("title", title || file.name);
    formData.append("grade", String(grade));

    try {
      const book = await apiUpload<BookRead>("/books", formData);
      if (book.status === "ready") {
        await createSessionAndRedirect(book);
      } else if (book.status === "failed") {
        setPhase("failed");
      } else {
        startPolling(book.id);
      }
    } catch (err) {
      setPhase("error");
      setErrorMessage(
        err instanceof ApiError ? (ERROR_MESSAGES[err.code] ?? err.message) : "Something went wrong. Please try again.",
      );
    }
  }

  function handleRetry() {
    if (pollTimer.current) clearInterval(pollTimer.current);
    if (slowNoteTimer.current) clearTimeout(slowNoteTimer.current);
    setPhase("idle");
    setErrorMessage(null);
    setShowSlowNote(false);
  }

  const isBusy = phase === "submitting" || phase === "processing" || phase === "creating_session";

  return (
    <div className="flex flex-1 items-center justify-center bg-gradient-to-br from-indigo-50 via-background to-amber-50 p-6 dark:from-indigo-950/30 dark:via-background dark:to-amber-950/10">
      <motion.div initial="hidden" animate="visible" variants={fadeRise} className="w-full max-w-xl">
        <Card className="[--card-spacing:--spacing(6)] shadow-lg">
          <CardHeader>
            <div className="mb-2 flex size-14 items-center justify-center rounded-xl bg-primary text-primary-foreground">
              <BookOpen className="size-7" />
            </div>
            <CardTitle className="text-2xl">Add your textbook</CardTitle>
            <CardDescription className="text-base">Upload the book you want to ask questions about.</CardDescription>
          </CardHeader>
          <CardContent>
            <AnimatePresence mode="wait">
              {phase === "idle" || phase === "submitting" || phase === "error" ? (
                <motion.form
                  key="form"
                  initial="hidden"
                  animate="visible"
                  exit="exit"
                  variants={fade}
                  onSubmit={handleSubmit}
                  className="flex flex-col gap-5"
                >
                  <div className="flex flex-col gap-2">
                    <Label htmlFor="file" className="text-base">Textbook PDF</Label>
                    <input
                      id="file"
                      type="file"
                      accept="application/pdf"
                      required
                      onChange={handleFileChange}
                      className="text-base file:mr-3 file:rounded-lg file:border-0 file:bg-secondary file:px-4 file:py-2 file:text-base file:font-medium"
                    />
                  </div>
                  <div className="flex flex-col gap-2">
                    <Label htmlFor="title" className="text-base">Title</Label>
                    <Input
                      id="title"
                      required
                      value={title}
                      onChange={(event) => {
                        setTitleTouched(true);
                        setTitle(event.target.value);
                      }}
                      className="h-11 text-base md:text-base"
                    />
                  </div>
                  <div className="flex flex-col gap-2">
                    <Label htmlFor="grade" className="text-base">Class</Label>
                    <select
                      id="grade"
                      required
                      value={grade}
                      onChange={(event) => setGrade(Number(event.target.value))}
                      className="border-input h-11 rounded-lg border bg-transparent px-3 text-base shadow-xs outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
                    >
                      {GRADES.map((g) => (
                        <option key={g} value={g}>
                          Class {g}
                        </option>
                      ))}
                    </select>
                  </div>
                  {errorMessage && (
                    <p role="alert" className="text-base text-destructive">
                      {errorMessage}
                    </p>
                  )}
                  <Button type="submit" disabled={!file || isBusy} size="lg" className="w-full text-base">
                    {phase === "submitting" ? "Uploading…" : "Upload and start"}
                  </Button>
                </motion.form>
              ) : phase === "failed" ? (
                <motion.div key="failed" initial="hidden" animate="visible" exit="exit" variants={fade} className="flex flex-col gap-5">
                  <p className="text-base text-destructive">
                    Something went wrong processing this book. Please try again, or ask your teacher for a different copy.
                  </p>
                  <Button onClick={handleRetry} variant="outline" size="lg" className="w-full text-base">
                    Try again
                  </Button>
                </motion.div>
              ) : (
                <motion.div key="checklist" initial="hidden" animate="visible" exit="exit" variants={fade} className="flex flex-col gap-5">
                  <motion.ul initial="hidden" animate="visible" variants={staggerContainer} className="flex flex-col gap-4">
                    {CHECKLIST_STEPS.map(({ label, icon: StepIcon }) => {
                      const done = phase === "creating_session";
                      return (
                        <motion.li key={label} variants={staggerItem} className="flex items-center gap-3 text-base">
                          <div
                            className={`flex size-8 shrink-0 items-center justify-center rounded-full ${
                              done ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground"
                            }`}
                          >
                            {done ? (
                              <Check className="size-4.5" />
                            ) : (
                              <StepIcon className="size-4 animate-pulse" />
                            )}
                          </div>
                          <span className={done ? "" : "text-muted-foreground"}>{label}</span>
                          {!done && <Loader2 className="ml-auto size-4 shrink-0 animate-spin text-muted-foreground" />}
                        </motion.li>
                      );
                    })}
                  </motion.ul>
                  {showSlowNote && phase === "processing" && (
                    <p className="text-sm text-muted-foreground">
                      This can take a few minutes the first time a book is added.
                    </p>
                  )}
                </motion.div>
              )}
            </AnimatePresence>
          </CardContent>
        </Card>
      </motion.div>
    </div>
  );
}
