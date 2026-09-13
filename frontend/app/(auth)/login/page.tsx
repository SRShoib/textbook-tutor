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
      <Card className="[--card-spacing:--spacing(6)]">
        <CardHeader>
          <CardTitle className="text-2xl">Welcome back</CardTitle>
          <CardDescription className="text-base">Log in to continue with your textbook.</CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="flex flex-col gap-5">
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
            <div className="flex flex-col gap-2">
              <Label htmlFor="password" className="text-base">Password</Label>
              <Input
                id="password"
                type="password"
                autoComplete="current-password"
                required
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                className="h-11 text-base md:text-base"
              />
            </div>
            {error && (
              <p role="alert" className="text-base text-destructive">
                {error}
              </p>
            )}
            <Button type="submit" disabled={submitting} size="lg" className="w-full text-base">
              {submitting ? "Logging in…" : "Log in"}
            </Button>
          </form>
          <p className="mt-5 text-center text-base text-muted-foreground">
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
