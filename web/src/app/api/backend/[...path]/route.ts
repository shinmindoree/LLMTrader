import { NextRequest, NextResponse } from "next/server";

import {
  clearSessionCookies,
  isSupabaseAuthEnabled,
  readSessionCookies,
  refreshSession,
  shouldRefreshSession,
  writeSessionCookies,
} from "@/lib/supabaseAuth";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const API_ORIGIN = process.env.API_ORIGIN ?? "http://localhost:8000";
const ADMIN_TOKEN = process.env.ADMIN_TOKEN ?? "dev-admin-token";

type ProxyAuthState = {
  accessToken: string;
  userId: string;
  refreshedSession:
    | {
        accessToken: string;
        refreshToken: string;
        expiresAt: number;
        userId: string;
        email: string | null;
      }
    | null;
  clearCookies: boolean;
};

async function resolveProxyAuth(req: NextRequest): Promise<ProxyAuthState | null> {
  if (!isSupabaseAuthEnabled()) {
    return null;
  }

  const session = readSessionCookies(req.cookies);
  if (!session) {
    return {
      accessToken: "",
      userId: "",
      refreshedSession: null,
      clearCookies: true,
    };
  }

  if (!shouldRefreshSession(session)) {
    return {
      accessToken: session.accessToken,
      userId: session.userId,
      refreshedSession: null,
      clearCookies: false,
    };
  }

  const refreshed = await refreshSession(session.refreshToken, session.userId, session.email);
  if (!refreshed) {
    return {
      accessToken: "",
      userId: "",
      refreshedSession: null,
      clearCookies: true,
    };
  }

  return {
    accessToken: refreshed.accessToken,
    userId: refreshed.userId,
    refreshedSession: refreshed,
    clearCookies: false,
  };
}

async function proxy(req: NextRequest, params: { path: string[] }): Promise<Response> {
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
  if (isSupabaseAuthEnabled() && (!auth || !auth.accessToken)) {
    const unauthorized = NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    if (auth?.clearCookies) {
      clearSessionCookies(unauthorized.cookies);
    }
    return unauthorized;
  }

  const headers = new Headers(req.headers);
  if (isSupabaseAuthEnabled() && auth) {
    headers.set("authorization", `Bearer ${auth.accessToken}`);
    headers.set("x-chat-user-id", auth.userId);
    headers.delete("x-admin-token");
  } else {
    headers.set("x-admin-token", ADMIN_TOKEN);
  }
  headers.delete("host");

  let body: ArrayBuffer | undefined;
  if (req.method !== "GET" && req.method !== "HEAD") {
    body = await req.arrayBuffer();
  }

  const res = await fetch(target.toString(), {
    method: req.method,
    headers,
    body,
    redirect: "manual",
  });

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
