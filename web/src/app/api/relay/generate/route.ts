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

const RELAY_ORIGIN = process.env.RELAY_SERVER_URL ?? "";
const RELAY_API_KEY = process.env.RELAY_API_KEY ?? "";

export async function POST(req: NextRequest): Promise<Response> {
  if (!RELAY_ORIGIN) {
    return new Response(
      JSON.stringify({ error: "RELAY_SERVER_URL is not configured" }),
      { status: 500, headers: { "content-type": "application/json" } },
    );
  }

  let refreshedSession: ReturnType<typeof readSessionCookies> = null;
  if (isSupabaseAuthEnabled()) {
    const session = readSessionCookies(req.cookies);
    if (!session) {
      const unauthorized = NextResponse.json({ error: "Unauthorized" }, { status: 401 });
      clearSessionCookies(unauthorized.cookies);
      return unauthorized;
    }
    if (shouldRefreshSession(session)) {
      refreshedSession = await refreshSession(session.refreshToken, session.userId, session.email);
      if (!refreshedSession) {
        const unauthorized = NextResponse.json({ error: "Unauthorized" }, { status: 401 });
        clearSessionCookies(unauthorized.cookies);
        return unauthorized;
      }
    }
  }

  const target = new URL("/generate", RELAY_ORIGIN);
  const body = await req.arrayBuffer();

  const headers = new Headers();
  headers.set("content-type", "application/json");
  if (RELAY_API_KEY) {
    headers.set("x-api-key", RELAY_API_KEY);
    headers.set("authorization", `Bearer ${RELAY_API_KEY}`);
  }

  const res = await fetch(target.toString(), {
    method: "POST",
    headers,
    body,
    redirect: "manual",
  });

  const resHeaders = new Headers(res.headers);
  resHeaders.delete("content-encoding");
  resHeaders.set("cache-control", "no-store");

  const response = new NextResponse(res.body, { status: res.status, headers: resHeaders });
  if (refreshedSession) {
    writeSessionCookies(response.cookies, refreshedSession);
  }
  return response;
}
