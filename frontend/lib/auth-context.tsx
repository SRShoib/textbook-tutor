"use client";

/**
 * What: session state for the whole app -- current user, auth status, and
 * login/register/logout. Wraps lib/api.ts's in-memory access token so
 * components never touch the token directly.
 * Why a silent refresh runs once on mount: the access token lives only in
 * memory (lib/api.ts), so a hard page reload always starts with no token.
 * The httpOnly refresh cookie the browser already holds is what recovers
 * the session, exactly like a real page load hitting POST /auth/refresh.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { apiFetch, refreshSession, setAccessToken } from "./api";
import type { LoginRequest, RegisterRequest, TokenResponse, UserRead } from "./types";

type AuthStatus = "loading" | "authed" | "anon";

interface AuthContextValue {
  user: UserRead | null;
  status: AuthStatus;
  login: (credentials: LoginRequest) => Promise<void>;
  register: (data: RegisterRequest) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<UserRead | null>(null);
  const [status, setStatus] = useState<AuthStatus>("loading");

  const applyTokenResponse = useCallback((tokenResponse: TokenResponse) => {
    setAccessToken(tokenResponse.access_token);
    setUser(tokenResponse.user);
    setStatus("authed");
  }, []);

  useEffect(() => {
    // Goes through the shared single-flight refreshSession() (lib/api.ts),
    // not a direct apiFetch call -- React Strict Mode runs this effect twice
    // on mount in dev, and two independent /auth/refresh requests racing on
    // the same cookie can trip the backend's replay-detection (api/auth.py)
    // and revoke the session that just succeeded. See lib/api.ts's comment.
    let cancelled = false;
    (async () => {
      const tokenResponse = await refreshSession();
      if (cancelled) return;
      if (tokenResponse) {
        setUser(tokenResponse.user);
        setStatus("authed");
      } else {
        setStatus("anon");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const login = useCallback(
    async (credentials: LoginRequest) => {
      const tokenResponse = await apiFetch<TokenResponse>("/auth/login", {
        method: "POST",
        body: credentials,
        skipAuthRetry: true,
      });
      applyTokenResponse(tokenResponse);
    },
    [applyTokenResponse],
  );

  const register = useCallback(
    async (data: RegisterRequest) => {
      const tokenResponse = await apiFetch<TokenResponse>("/auth/register", {
        method: "POST",
        body: data,
        skipAuthRetry: true,
      });
      applyTokenResponse(tokenResponse);
    },
    [applyTokenResponse],
  );

  const logout = useCallback(async () => {
    try {
      await apiFetch<void>("/auth/logout", { method: "POST", skipAuthRetry: true });
    } finally {
      setAccessToken(null);
      setUser(null);
      setStatus("anon");
    }
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({ user, status, login, register, logout }),
    [user, status, login, register, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within an AuthProvider");
  return ctx;
}
