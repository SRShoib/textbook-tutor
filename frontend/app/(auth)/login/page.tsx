"use client";

import { useState, type FormEvent } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { motion } from "framer-motion";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { useAuth } from "@/lib/auth-context";
import { ApiError } from "@/lib/api";
import { fadeRise } from "@/lib/motion";

// core/errors.py's AppError codes for this route (api/auth.py), mapped to
// wording a student can act on instead of raw backend text.
const ERROR_MESSAGES: Record<string, string> = {
  INVALID_CREDENTIALS: "That email or password isn't right. Please try again.",
  RATE_LIMITED: "Too many attempts. Please wait a few minutes and try again.",
};

export default function LoginPage() {
  const { login } = useAuth();
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await login({ email, password });
      router.replace("/upload");
    } catch (err) {
      setError(
        err instanceof ApiError
          ? (ERROR_MESSAGES[err.code] ?? err.message)
          : "Something went wrong. Please try again.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <motion.div initial="hidden" animate="visible" variants={fadeRise}>
      <Card>
        <CardHeader>
          <CardTitle>Welcome back</CardTitle>
          <CardDescription>Log in to continue with your textbook.</CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="flex flex-col gap-4">
            <div className="flex flex-col gap-2">
              <Label htmlFor="email">Email</Label>
              <Input
                id="email"
                type="email"
                autoComplete="email"
                required
                value={email}
                onChange={(event) => setEmail(event.target.value)}
              />
            </div>
            <div className="flex flex-col gap-2">
              <Label htmlFor="password">Password</Label>
              <Input
                id="password"
                type="password"
                autoComplete="current-password"
                required
                value={password}
                onChange={(event) => setPassword(event.target.value)}
              />
            </div>
            {error && (
              <p role="alert" className="text-sm text-destructive">
                {error}
              </p>
            )}
            <Button type="submit" disabled={submitting} className="w-full">
              {submitting ? "Logging in…" : "Log in"}
            </Button>
          </form>
          <p className="mt-4 text-center text-sm text-muted-foreground">
            New here?{" "}
            <Link href="/register" className="text-primary underline underline-offset-4">
              Create an account
            </Link>
          </p>
        </CardContent>
      </Card>
    </motion.div>
  );
}
