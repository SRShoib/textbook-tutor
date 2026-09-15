"use client";

/**
 * What: request a password reset link. Always shows the same confirmation
 * regardless of whether the email actually has an account -- matches
 * api/auth.py's forgot_password(), which returns an identical response
 * either way so this page can't be used to discover who has an account.
 */

import { useState, type FormEvent } from "react";
import Link from "next/link";
import { motion, AnimatePresence } from "framer-motion";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { apiFetch, ApiError } from "@/lib/api";
import { fadeRise, fade } from "@/lib/motion";

const ERROR_MESSAGES: Record<string, string> = {
  RATE_LIMITED: "Too many attempts. Please wait a few minutes and try again.",
};

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await apiFetch("/auth/forgot-password", { method: "POST", body: { email } });
      setSent(true);
    } catch (err) {
      setError(err instanceof ApiError ? (ERROR_MESSAGES[err.code] ?? err.message) : "Something went wrong. Please try again.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <motion.div initial="hidden" animate="visible" variants={fadeRise}>
      <Card className="[--card-spacing:--spacing(6)]">
        <CardHeader>
          <CardTitle className="text-2xl">Reset your password</CardTitle>
          <CardDescription className="text-base">
            Enter your email and we'll send you a link to reset it.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <AnimatePresence mode="wait">
            {sent ? (
              <motion.div key="sent" initial="hidden" animate="visible" exit="exit" variants={fade}>
                <p className="text-base">
                  If that email has an account, we've sent a link to reset the password. Check your inbox.
                </p>
                <p className="mt-5 text-center text-base text-muted-foreground">
                  <Link href="/login" className="text-primary underline underline-offset-4">
                    Back to log in
                  </Link>
                </p>
              </motion.div>
            ) : (
              <motion.form key="form" initial="hidden" animate="visible" exit="exit" variants={fade} onSubmit={handleSubmit} className="flex flex-col gap-5">
                <div className="flex flex-col gap-2">
                  <Label htmlFor="email" className="text-base">Email</Label>
                  <Input
                    id="email"
                    type="email"
                    autoComplete="email"
                    required
                    value={email}
                    onChange={(event) => setEmail(event.target.value)}
                    className="h-11 text-base md:text-base"
                  />
                </div>
                {error && (
                  <p role="alert" className="text-base text-destructive">
                    {error}
                  </p>
                )}
                <Button type="submit" disabled={submitting} size="lg" className="w-full text-base">
                  {submitting ? "Sending…" : "Send reset link"}
                </Button>
                <p className="text-center text-base text-muted-foreground">
                  <Link href="/login" className="text-primary underline underline-offset-4">
                    Back to log in
                  </Link>
                </p>
              </motion.form>
            )}
          </AnimatePresence>
        </CardContent>
      </Card>
    </motion.div>
  );
}
