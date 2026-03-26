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
  );
}

const PROXY_TIMEOUT_MS = 120_000;

async function proxy(req: NextRequest, params: { path: string[] }): Promise<Response> {
  const clientIp = req.headers.get("x-forwarded-for")?.split(",")[0]?.trim() ?? "unknown";
  if (isRateLimited(clientIp)) {
    return NextResponse.json(
      { error: "Too many requests" },
      { status: 429, headers: getRateLimitHeaders(clientIp) },
    );
  }

  const url = new URL(req.url);
  const relPath = params.path.join("/");
  const target = new URL(relPath, API_ORIGIN.endsWith("/") ? API_ORIGIN : `${API_ORIGIN}/`);
  target.search = url.search;

  let proxyAuth: ProxyAuthState | null = null;
  try {
    proxyAuth = await resolveProxyAuth();
  } catch (error) {
    return NextResponse.json(
      { error: error instanceof Error ? error.message : "Auth initialization failed" },
      { status: 500 },
    );
  }
  if (!proxyAuth) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
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

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), PROXY_TIMEOUT_MS);

  let res: globalThis.Response;
  try {
    res = await fetch(target.toString(), {
      method: req.method,
      headers,
      body,
      redirect: "manual",
      signal: controller.signal,
    });
  } catch (err) {
    clearTimeout(timeout);
    const message = err instanceof Error ? err.message : "Backend request failed";
    const status = controller.signal.aborted ? 504 : 502;
    return NextResponse.json({ error: message }, { status });
  }
  clearTimeout(timeout);

  const resHeaders = new Headers(res.headers);
  resHeaders.delete("content-encoding");

  // If backend returned a non-JSON error (e.g. HTML 502), normalise to JSON
  if (!res.ok && !res.headers.get("content-type")?.includes("application/json")) {
    const text = await res.text().catch(() => "");
    return NextResponse.json(
      { error: text.slice(0, 200) || `Backend returned ${res.status}` },
      { status: res.status },
    );
  }

  // Allow short-lived browser cache for read-only listing endpoints
  const isCacheableListing =
    req.method === "GET" &&
    res.ok &&
    /^api\/(strategies\/chat\/sessions\/list|strategies\/list|jobs\/list)/.test(relPath);
  resHeaders.set("cache-control", isCacheableListing ? "private, max-age=5" : "no-store");

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
