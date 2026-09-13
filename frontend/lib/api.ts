/**
 * The one fetch wrapper every API call in this app goes through -- mirrors
 * CLAUDE.md's "one function" rule for call_llm() on the frontend side, so
 * auth headers, the {"error":{code,message}} shape (core/errors.py) and the
 * 401-refresh-and-retry dance are handled in exactly one place.
 */

import type { ApiErrorBody, TokenResponse } from "./types";

export const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
export const API_PREFIX = "/api/v1";

export class ApiError extends Error {
  code: string;
  status: number;

  constructor(code: string, message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
  }
}

// Access tokens live only here, in memory -- never localStorage/sessionStorage.
// These are children's accounts; an in-memory-only token is not reachable by
// an XSS payload that runs after the page loads and reads storage. The cost
// is a hard refresh always needs the silent-refresh-on-mount in
// auth-context.tsx to restore the session, since this variable resets to null.
let accessToken: string | null = null;

export function setAccessToken(token: string | null): void {
  accessToken = token;
}

export function getAccessToken(): string | null {
  return accessToken;
}

// api/auth.py:157-169 treats a *replayed* refresh token as evidence of theft
// and revokes every active refresh token for that user. Any two callers
// hitting this at the same moment -- lib/auth-context.tsx's mount-time
// silent refresh (React Strict Mode double-invokes effects in dev, firing
// it twice) and apiFetch's own 401-retry below -- must not fire two separate
// requests with the same cookie: the loser of that race gets treated as a
// replay and everyone's tokens get revoked. Every caller, everywhere in the
// app, goes through this one shared in-flight promise instead.
let refreshInFlight: Promise<TokenResponse | null> | null = null;

export async function refreshSession(): Promise<TokenResponse | null> {
  if (refreshInFlight) return refreshInFlight;

  refreshInFlight = (async () => {
    try {
      const res = await fetch(`${API_BASE}${API_PREFIX}/auth/refresh`, {
        method: "POST",
        credentials: "include",
      });
      if (!res.ok) {
        setAccessToken(null);
        return null;
      }
      const body = (await res.json()) as TokenResponse;
      setAccessToken(body.access_token);
      return body;
    } catch {
      setAccessToken(null);
      return null;
    } finally {
      refreshInFlight = null;
    }
  })();

  return refreshInFlight;
}

async function parseErrorBody(res: Response): Promise<ApiErrorBody["error"]> {
  const fallback = { code: "UNKNOWN_ERROR", message: `Request failed with status ${res.status}` };
  try {
    const body = (await res.json()) as Partial<ApiErrorBody>;
    // A FastAPI validation 422 (Pydantic) doesn't go through core/errors.py's
    // AppError handler and won't have this shape -- fall back rather than
    // throw on a malformed field.
    return body.error ?? fallback;
  } catch {
    return fallback;
  }
}

interface ApiFetchOptions extends Omit<RequestInit, "body"> {
  body?: unknown;
  /** Skip the automatic refresh-and-retry-once on 401. Used by login/register/refresh
   * themselves, where a 401 means "wrong credentials", not "expired token". */
  skipAuthRetry?: boolean;
}

export async function apiFetch<T>(path: string, options: ApiFetchOptions = {}): Promise<T> {
  const { body, skipAuthRetry, headers, ...rest } = options;

  const doFetch = (): Promise<Response> => {
    const token = getAccessToken();
    return fetch(`${API_BASE}${API_PREFIX}${path}`, {
      ...rest,
      credentials: "include",
      headers: {
        ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...headers,
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  };

  let res = await doFetch();

  if (res.status === 401 && !skipAuthRetry) {
    const refreshed = await refreshSession();
    if (refreshed) {
      res = await doFetch();
    }
  }

  if (!res.ok) {
    const { code, message } = await parseErrorBody(res);
    throw new ApiError(code, message, res.status);
  }

  if (res.status === 204) {
    return undefined as T;
  }

  return (await res.json()) as T;
}

/**
 * For multipart uploads (POST /books) -- apiFetch always JSON-encodes
 * `body`, which doesn't work for a File. Same auth header injection,
 * 401-retry-once, and ApiError unwrapping; no Content-Type is set so the
 * browser can add its own multipart boundary.
 */
export async function apiUpload<T>(path: string, formData: FormData): Promise<T> {
  const doFetch = (): Promise<Response> => {
    const token = getAccessToken();
    return fetch(`${API_BASE}${API_PREFIX}${path}`, {
      method: "POST",
      credentials: "include",
      headers: token ? { Authorization: `Bearer ${token}` } : undefined,
      body: formData,
    });
  };

  let res = await doFetch();

  if (res.status === 401) {
    const refreshed = await refreshSession();
    if (refreshed) {
      res = await doFetch();
    }
  }

  if (!res.ok) {
    const { code, message } = await parseErrorBody(res);
    throw new ApiError(code, message, res.status);
  }

  return (await res.json()) as T;
}
