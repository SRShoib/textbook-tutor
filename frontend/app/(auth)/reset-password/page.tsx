"use client";

/**
 * What: set a new password from the link forgot-password emailed (or
 * printed to the console -- see backend/app/core/email.py). The token
 * lives in the URL's ?token= query param, which Next.js requires
 * useSearchParams() to be read inside a Suspense boundary for, hence the
 * inner/outer component split below.
 */

import { Suspense, useState, type FormEvent } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { motion } from "framer-motion";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { apiFetch, ApiError } from "@/lib/api";
import { fadeRise } from "@/lib/motion";

const ERROR_MESSAGES: Record<string, string> = {
  TOKEN_INVALID: "This reset link isn't valid. It may have already been used.",
  TOKEN_EXPIRED: "This reset link has expired.",
};

function ResetPasswordForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const token = searchParams.get("token");

  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);

    if (newPassword !== confirmPassword) {
      setError("Passwords don't match.");
      return;
    }
    if (!token) {
      setError("This reset link is missing its token.");
      return;
    }

    setSubmitting(true);
    try {
      await apiFetch("/auth/reset-password", { method: "POST", body: { token, new_password: newPassword } });
      toast.success("Password updated. Please log in.");
      router.replace("/login");
    } catch (err) {
      setError(err instanceof ApiError ? (ERROR_MESSAGES[err.code] ?? err.message) : "Something went wrong. Please try again.");
    } finally {
      setSubmitting(false);
    }
  }

  if (!token) {
    return (
      <div className="flex flex-col gap-4">
        <p className="text-base text-destructive">This reset link is missing its token.</p>
        <p className="text-center text-base text-muted-foreground">
          <Link href="/forgot-password" className="text-primary underline underline-offset-4">
            Request a new link
          </Link>
        </p>
      </div>
    );
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-5">
      <div className="flex flex-col gap-2">
        <Label htmlFor="new_password" className="text-base">New password</Label>
        <Input
          id="new_password"
          type="password"
          autoComplete="new-password"
          required
          minLength={8}
          value={newPassword}
          onChange={(event) => setNewPassword(event.target.value)}
          className="h-11 text-base md:text-base"
        />
        <p className="text-sm text-muted-foreground">At least 8 characters.</p>
      </div>
      <div className="flex flex-col gap-2">
        <Label htmlFor="confirm_password" className="text-base">Confirm new password</Label>
        <Input
          id="confirm_password"
          type="password"
          autoComplete="new-password"
          required
          minLength={8}
          value={confirmPassword}
          onChange={(event) => setConfirmPassword(event.target.value)}
          className="h-11 text-base md:text-base"
        />
      </div>
      {error && (
        <div className="flex flex-col gap-2">
          <p role="alert" className="text-base text-destructive">
            {error}
          </p>
          {(error.includes("expired") || error.includes("already been used") || error.includes("isn't valid")) && (
            <Link href="/forgot-password" className="text-base text-primary underline underline-offset-4">
              Request a new link
            </Link>
          )}
        </div>
      )}
      <Button type="submit" disabled={submitting} size="lg" className="w-full text-base">
        {submitting ? "Saving…" : "Set new password"}
      </Button>
    </form>
  );
}

export default function ResetPasswordPage() {
  return (
    <motion.div initial="hidden" animate="visible" variants={fadeRise}>
      <Card className="[--card-spacing:--spacing(6)]">
        <CardHeader>
          <CardTitle className="text-2xl">Set a new password</CardTitle>
          <CardDescription className="text-base">Choose a new password for your account.</CardDescription>
        </CardHeader>
        <CardContent>
          <Suspense fallback={<p className="text-base text-muted-foreground">Loading…</p>}>
            <ResetPasswordForm />
          </Suspense>
        </CardContent>
      </Card>
    </motion.div>
  );
}
