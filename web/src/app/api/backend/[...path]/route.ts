import { NextRequest, NextResponse } from "next/server";

import { isAdminEmail } from "@/lib/admin";
import { isRateLimited, getRateLimitHeaders } from "@/lib/rateLimit";
import {
  clearSessionCookies,
  isAuthEnabled,
  readSessionCookies,
  refreshAccessToken,
  shouldRefreshSession,
  writeSessionCookies,
  type SessionSnapshot,
} from "@/lib/entraAuth";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const API_ORIGIN = process.env.API_ORIGIN ?? "http://localhost:8000";
const ADMIN_TOKEN = process.env.ADMIN_TOKEN ?? "";

type ProxyAuthState = {
  accessToken: string;
  userId: string;
  email: string | null;
  refreshedSession: SessionSnapshot | null;
  clearCookies: boolean;
};

async function resolveProxyAuth(req: NextRequest): Promise<ProxyAuthState | null> {
  if (!isAuthEnabled()) {
    return null;
  }

  const session = readSessionCookies(req.cookies);
  if (!session) {
    return {
      accessToken: "",
      userId: "",
      email: null,
      refreshedSession: null,
      clearCookies: true,
    };
  }

  if (!shouldRefreshSession(session)) {
    return {
      accessToken: session.idToken,
      userId: session.userId,
      email: session.email,
      refreshedSession: null,
      clearCookies: false,
    };
  }

  const refreshed = await refreshAccessToken(session.refreshToken);
  if (!refreshed) {
    return {
      accessToken: "",
      userId: "",
      email: null,
      refreshedSession: null,
      clearCookies: true,
    };
  }

  return {
    accessToken: refreshed.idToken,
    userId: refreshed.userId,
    email: refreshed.email,
    refreshedSession: refreshed,
    clearCookies: false,
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

  let auth: ProxyAuthState | null = null;
  try {
    auth = await resolveProxyAuth(req);
  } catch (error) {
    return NextResponse.json(
      { error: error instanceof Error ? error.message : "Auth initialization failed" },
      { status: 500 },
    );
  }
  if (isAuthEnabled() && (!auth || !auth.accessToken)) {
    const unauthorized = NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    if (auth?.clearCookies) {
      clearSessionCookies(unauthorized.cookies);
    }
    return unauthorized;
  }
  if (isAdminOnlyPath(relPath) && !isAdminEmail(auth?.email)) {
    return NextResponse.json({ error: "Admin access required" }, { status: 403 });
  }

  const headers = new Headers(req.headers);
  if (isAuthEnabled() && auth) {
    headers.set("authorization", `Bearer ${auth.accessToken}`);
    headers.set("x-chat-user-id", auth.userId);
    headers.delete("x-admin-token");
  } else {
    if (!ADMIN_TOKEN) {
      console.warn("[proxy] ADMIN_TOKEN env var is not set — admin routes will fail");
    }
    headers.set("x-admin-token", ADMIN_TOKEN);
  }
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
  resHeaders.set("cache-control", "no-store");

  const response = new NextResponse(res.body, { status: res.status, headers: resHeaders });
  if (auth?.refreshedSession) {
    writeSessionCookies(response.cookies, auth.refreshedSession);
  }
  if (auth?.clearCookies) {
    clearSessionCookies(response.cookies);
  }
  return response;
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
