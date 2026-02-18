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
    return NextResponse.json(
      { error: "RELAY_SERVER_URL is not configured" },
      { status: 500 },
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

  let body: { input?: string };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
  }

  const target = new URL("/test", RELAY_ORIGIN);
  const headers = new Headers();
  headers.set("content-type", "application/json");
  if (RELAY_API_KEY) {
    headers.set("x-api-key", RELAY_API_KEY);
    headers.set("authorization", `Bearer ${RELAY_API_KEY}`);
  }

  const res = await fetch(target.toString(), {
    method: "POST",
    headers,
    body: JSON.stringify({ input: String(body?.input ?? "").trim() || "Hello" }),
    redirect: "manual",
  });

  const resBody = await res.text();
  let json: unknown;
  try {
    json = JSON.parse(resBody);
  } catch {
    json = { error: resBody || "Unknown error" };
  }

  const response = NextResponse.json(json, {
    status: res.status,
    headers: { "cache-control": "no-store" },
  });
  if (refreshedSession) {
    writeSessionCookies(response.cookies, refreshedSession);
  }
  return response;
}
