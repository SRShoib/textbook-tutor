"use client";

/**
 * What: the screen for changing your own display name and class (2026-09-15
 * admin-owned-books decision: class is chosen at registration and changed
 * here, never per chat). Calls PATCH /auth/me (module 2) via
 * auth-context.tsx's updateProfile.
 */

import { useState, type FormEvent } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useAuth } from "@/lib/auth-context";
import { ApiError } from "@/lib/api";

const GRADES = Array.from({ length: 12 }, (_, i) => i + 1);

export default function ProfilePage() {
  const { user, updateProfile } = useAuth();
  const [displayName, setDisplayName] = useState(user?.display_name ?? "");
  const [grade, setGrade] = useState(user?.grade ?? 5);
  const [saving, setSaving] = useState(false);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    try {
      await updateProfile({ display_name: displayName, grade });
      toast.success("Profile updated.");
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Could not update your profile.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="flex-1 overflow-y-auto p-6 lg:p-10">
      <div className="mx-auto flex max-w-2xl flex-col gap-8">
        <div>
          <h1 className="font-heading text-3xl font-semibold tracking-tight">Your profile</h1>
          <p className="mt-1.5 text-base text-muted-foreground">Update your name or class.</p>
        </div>

        <Card className="[--card-spacing:--spacing(6)]">
          <CardHeader>
            <CardTitle className="text-lg">Account details</CardTitle>
            <CardDescription className="text-base">Email can't be changed here.</CardDescription>
          </CardHeader>
          <CardContent>
            <form onSubmit={handleSubmit} className="flex flex-col gap-5">
              <div className="flex flex-col gap-2">
                <Label htmlFor="email" className="text-base">Email</Label>
                <Input id="email" value={user?.email ?? ""} disabled className="h-11 text-base md:text-base" />
              </div>
              <div className="flex flex-col gap-2">
                <Label htmlFor="display_name" className="text-base">Name</Label>
                <Input
                  id="display_name"
                  required
                  value={displayName}
                  onChange={(event) => setDisplayName(event.target.value)}
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
                <p className="text-sm text-muted-foreground">
                  New chats will use this class — older chats keep the class they were started with.
                </p>
              </div>
              <Button type="submit" disabled={saving} size="lg" className="w-full text-base sm:w-fit">
                {saving ? "Saving…" : "Save changes"}
              </Button>
            </form>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
