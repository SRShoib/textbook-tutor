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
    <div className="flex-1 overflow-y-auto p-6">
      <div className="mx-auto flex max-w-md flex-col gap-6">
        <div>
          <h1 className="text-2xl font-semibold">Your profile</h1>
          <p className="text-muted-foreground">Update your name or class.</p>
        </div>

        <Card>
          <CardHeader>
            <CardTitle>Account details</CardTitle>
            <CardDescription>Email can't be changed here.</CardDescription>
          </CardHeader>
          <CardContent>
            <form onSubmit={handleSubmit} className="flex flex-col gap-4">
              <div className="flex flex-col gap-2">
                <Label htmlFor="email">Email</Label>
                <Input id="email" value={user?.email ?? ""} disabled />
              </div>
              <div className="flex flex-col gap-2">
                <Label htmlFor="display_name">Name</Label>
                <Input
                  id="display_name"
                  required
                  value={displayName}
                  onChange={(event) => setDisplayName(event.target.value)}
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
                <p className="text-sm text-muted-foreground">
                  New chats will use this class — older chats keep the class they were started with.
                </p>
              </div>
              <Button type="submit" disabled={saving} className="w-fit">
                {saving ? "Saving…" : "Save changes"}
              </Button>
            </form>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
