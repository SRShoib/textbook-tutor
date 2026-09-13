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
import { Check, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useAuth } from "@/lib/auth-context";
import { apiFetch, apiUpload, ApiError } from "@/lib/api";
import type { BookRead, SessionRead } from "@/lib/types";
import { fadeRise, fade } from "@/lib/motion";

const GRADES = Array.from({ length: 12 }, (_, i) => i + 1);

const CHECKLIST_STEPS = [
  "Reading your book",
  "Splitting it into lessons",
  "Understanding the vocabulary",
  "Getting ready to answer questions",
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
    <div className="flex flex-1 items-center justify-center p-6">
      <motion.div initial="hidden" animate="visible" variants={fadeRise} className="w-full max-w-md">
        <Card>
          <CardHeader>
            <CardTitle>Add your textbook</CardTitle>
            <CardDescription>Upload the book you want to ask questions about.</CardDescription>
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
                  className="flex flex-col gap-4"
                >
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
                      className="border-input h-9 rounded-lg border bg-transparent px-3 text-sm shadow-xs outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
                    >
                      {GRADES.map((g) => (
                        <option key={g} value={g}>
                          Class {g}
                        </option>
                      ))}
                    </select>
                  </div>
                  {errorMessage && (
                    <p role="alert" className="text-sm text-destructive">
                      {errorMessage}
                    </p>
                  )}
                  <Button type="submit" disabled={!file || isBusy} className="w-full">
                    {phase === "submitting" ? "Uploading…" : "Upload and start"}
                  </Button>
                </motion.form>
              ) : phase === "failed" ? (
                <motion.div key="failed" initial="hidden" animate="visible" exit="exit" variants={fade} className="flex flex-col gap-4">
                  <p className="text-sm text-destructive">
                    Something went wrong processing this book. Please try again, or ask your teacher for a different copy.
                  </p>
                  <Button onClick={handleRetry} variant="outline" className="w-full">
                    Try again
                  </Button>
                </motion.div>
              ) : (
                <motion.div key="checklist" initial="hidden" animate="visible" exit="exit" variants={fade} className="flex flex-col gap-4">
                  <ul className="flex flex-col gap-3">
                    {CHECKLIST_STEPS.map((step) => (
                      <li key={step} className="flex items-center gap-3 text-sm">
                        {phase === "creating_session" ? (
                          <Check className="size-4 shrink-0 text-primary" />
                        ) : (
                          <Loader2 className="size-4 shrink-0 animate-spin text-muted-foreground" />
                        )}
                        <span className={phase === "creating_session" ? "" : "text-muted-foreground"}>{step}</span>
                      </li>
                    ))}
                  </ul>
                  {showSlowNote && phase === "processing" && (
                    <p className="text-xs text-muted-foreground">
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
