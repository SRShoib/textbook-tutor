"use client";

/**
 * What: role guard for everything under /admin. The parent (app) layout
 * already blocks rendering until status === "authed", so by the time this
 * runs `user` is guaranteed non-null -- this only adds the role check on top.
 * Why redirect to /chat/new rather than show a "not allowed" page: that's
 * the same screen every other student already lands on, so a non-admin
 * poking at /admin directly just ends up wherever they'd normally be.
 */

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth-context";

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  const { user } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (user && user.role !== "admin") router.replace("/chat/new");
  }, [user, router]);

  if (!user || user.role !== "admin") return null;

  return <>{children}</>;
}
