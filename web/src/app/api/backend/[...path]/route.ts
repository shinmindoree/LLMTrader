import { NextRequest, NextResponse } from "next/server";

import { isAdminEmail } from "@/lib/admin";
import { isRateLimited, getRateLimitHeaders } from "@/lib/rateLimit";
import { auth } from "@/auth";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const API_ORIGIN = process.env.API_ORIGIN ?? "http://localhost:8000";
const AUTH_SECRET = process.env.AUTH_SECRET ?? "";

type ProxyAuthState = {
  token: string;
  userId: string;
  email: string | null;
};

async function resolveProxyAuth(): Promise<ProxyAuthState | null> {
  const session = await auth();
  if (!session?.user) return null;
  return {
    token: AUTH_SECRET, // Backend will verify via shared secret
    userId: session.user.id ?? "",
    email: session.user.email ?? null,
  };
}

function isAdminOnlyPath(path: string): boolean {
  return (
    path === "api/llm-test"
    || path === "api/strategies/capabilities"
    || path === "api/strategies/quality/summary"
    || path.startsWith("api/admin/")
  );
}

const PROXY_TIMEOUT_MS = 120_000;

// Transient upstream errors (Container Apps cold start / pod restart) are surfaced by
// the Envoy ingress as 502/503/504. Retry idempotent requests a few times with a short
// backoff before failing the user-visible request.
const RETRY_STATUSES = new Set([502, 503, 504]);
const RETRY_BACKOFF_MS = [200, 600, 1200];

function isRetriableMethod(method: string): boolean {
  return method === "GET" || method === "HEAD";
}

function isStreamingPath(relPath: string): boolean {
  return /\/(stream|live\/stream)$/.test(relPath);
}

async function fetchWithRetry(
  target: string,
  init: RequestInit,
  relPath: string,
  method: string,
): Promise<{ res: globalThis.Response | null; error: unknown; aborted: boolean }> {
  const canRetry = isRetriableMethod(method) && !isStreamingPath(relPath);
  const maxAttempts = canRetry ? RETRY_BACKOFF_MS.length + 1 : 1;
  let lastError: unknown = null;
  let aborted = false;

  for (let attempt = 0; attempt < maxAttempts; attempt++) {
    try {
      const res = await fetch(target, init);
      if (!RETRY_STATUSES.has(res.status) || attempt === maxAttempts - 1 || !canRetry) {
        return { res, error: null, aborted: false };
      }
      // Drain body so the connection can be reused, then retry
      try {
        await res.arrayBuffer();
      } catch {
        // ignore
      }
      console.warn(
        `[proxy] Backend ${res.status} for ${relPath} (attempt ${attempt + 1}/${maxAttempts}), retrying...`,
      );
    } catch (err) {
      lastError = err;
      const signal = (init.signal as AbortSignal | undefined);
      if (signal?.aborted) {
        aborted = true;
        return { res: null, error: err, aborted: true };
      }
      if (!canRetry || attempt === maxAttempts - 1) {
        return { res: null, error: err, aborted: false };
      }
      console.warn(
        `[proxy] Backend fetch failed for ${relPath} (attempt ${attempt + 1}/${maxAttempts}):`,
        err instanceof Error ? err.message : String(err),
      );
    }
    const delay = RETRY_BACKOFF_MS[attempt] ?? 1200;
    await new Promise((resolve) => setTimeout(resolve, delay));
  }
  return { res: null, error: lastError, aborted };
}

async function proxy(req: NextRequest, params: { path: string[] }): Promise<Response> {
  const url = new URL(req.url);
  const relPath = params.path.join("/");
  const target = new URL(relPath, API_ORIGIN.endsWith("/") ? API_ORIGIN : `${API_ORIGIN}/`);
  target.search = url.search;

  let proxyAuth: ProxyAuthState | null = null;
  try {
    proxyAuth = await resolveProxyAuth();
  } catch (error) {
    console.error("[proxy] resolveProxyAuth() failed:", error);
    return NextResponse.json(
      { error: "Auth service error", detail: error instanceof Error ? error.message : "Unknown" },
      { status: 500 },
    );
  }
  if (!proxyAuth) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  // Rate limit by userId (falls back to IP if no userId)
  const rateLimitKey = proxyAuth.userId || req.headers.get("x-forwarded-for")?.split(",")[0]?.trim() || "unknown";
  if (isRateLimited(rateLimitKey, req.method)) {
    return NextResponse.json(
      { error: "Too many requests" },
      { status: 429, headers: getRateLimitHeaders(rateLimitKey, req.method) },
    );
  }

  if (isAdminOnlyPath(relPath) && !isAdminEmail(proxyAuth.email)) {
    return NextResponse.json({ error: "Admin access required" }, { status: 403 });
  }

  const headers = new Headers(req.headers);
  headers.set("authorization", `Bearer ${proxyAuth.token}`);
  headers.set("x-chat-user-id", proxyAuth.userId);
  if (proxyAuth.email) {
    headers.set("x-user-email", proxyAuth.email);
  }
  headers.delete("x-admin-token");
  headers.delete("host");

  let body: ArrayBuffer | undefined;
  if (req.method !== "GET" && req.method !== "HEAD") {
    body = await req.arrayBuffer();
  }

  // SSE/streaming endpoints should not have a timeout — they're long-lived connections
  const isStreamEndpoint = isStreamingPath(relPath);
  const controller = new AbortController();
  const timeout = isStreamEndpoint ? null : setTimeout(() => controller.abort(), PROXY_TIMEOUT_MS);

  const { res: fetched, error: fetchErr, aborted } = await fetchWithRetry(
    target.toString(),
    {
      method: req.method,
      headers,
      body,
      redirect: "manual",
      signal: controller.signal,
    },
    relPath,
    req.method,
  );
  if (timeout) clearTimeout(timeout);

  if (!fetched) {
    const message = fetchErr instanceof Error ? fetchErr.message : "Backend request failed";
    const status = aborted ? 504 : 502;
    console.error(`[proxy] fetch to ${target.pathname} failed (${status}):`, message);
    return NextResponse.json({ error: message }, { status });
  }
  const res = fetched;

  const resHeaders = new Headers(res.headers);
  resHeaders.delete("content-encoding");

  // If backend returned a non-JSON error (e.g. HTML 502), normalise to JSON
  if (!res.ok && !res.headers.get("content-type")?.includes("application/json")) {
    const text = await res.text().catch(() => "");
    console.error(`[proxy] Backend ${res.status} (non-JSON) for ${relPath}:`, text.slice(0, 200));
    return NextResponse.json(
      { error: text.slice(0, 200) || `Backend returned ${res.status}`, path: relPath },
      { status: res.status },
    );
  }

  // Allow short-lived browser cache for read-only listing endpoints
  const isCacheableListing =
    req.method === "GET" &&
    res.ok &&
    /^api\/(strategies\/chat\/sessions\/list|strategies\/list|jobs\/list)/.test(relPath);
  const isSSE = res.headers.get("content-type")?.includes("text/event-stream");
  if (isSSE) {
    resHeaders.set("cache-control", "no-cache");
    resHeaders.set("connection", "keep-alive");
  } else {
    resHeaders.set("cache-control", isCacheableListing ? "private, max-age=5" : "no-store");
  }

  return new NextResponse(res.body, { status: res.status, headers: resHeaders });
}

type RouteCtx = { params: Promise<{ path: string[] }> };

export async function GET(req: NextRequest, ctx: RouteCtx): Promise<Response> {
  const params = await ctx.params;
  return proxy(req, params);
}

export async function POST(req: NextRequest, ctx: RouteCtx): Promise<Response> {
  const params = await ctx.params;
  return proxy(req, params);
}

export async function PUT(req: NextRequest, ctx: RouteCtx): Promise<Response> {
  const params = await ctx.params;
  return proxy(req, params);
}

export async function PATCH(req: NextRequest, ctx: RouteCtx): Promise<Response> {
  const params = await ctx.params;
  return proxy(req, params);
}

export async function DELETE(req: NextRequest, ctx: RouteCtx): Promise<Response> {
  const params = await ctx.params;
  return proxy(req, params);
}
