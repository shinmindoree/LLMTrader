import { NextRequest, NextResponse } from "next/server";

import {
  clearSessionCookies,
  fetchSupabaseUser,
  isSupabaseAuthEnabled,
  readSessionCookies,
  refreshSession,
  shouldRefreshSession,
  writeSessionCookies,
} from "@/lib/supabaseAuth";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(req: NextRequest): Promise<Response> {
  if (!isSupabaseAuthEnabled()) {
    return NextResponse.json(
      { authenticated: false, reason: "disabled" },
      { status: 200 },
    );
  }

  const current = readSessionCookies(req.cookies);
  if (!current) {
    const response = NextResponse.json({ authenticated: false }, { status: 401 });
    clearSessionCookies(response.cookies);
    return response;
  }

  let resolved = current;
  let refreshed = false;

  if (shouldRefreshSession(current)) {
    const nextSession = await refreshSession(current.refreshToken, current.userId, current.email);
    if (!nextSession) {
      const response = NextResponse.json({ authenticated: false }, { status: 401 });
      clearSessionCookies(response.cookies);
      return response;
    }
    resolved = nextSession;
    refreshed = true;
  }

  const user = await fetchSupabaseUser(resolved.accessToken);
  if (!user) {
    const response = NextResponse.json({ authenticated: false }, { status: 401 });
    clearSessionCookies(response.cookies);
    return response;
  }

  const response = NextResponse.json(
    { authenticated: true, user: { id: user.id, email: user.email } },
    { status: 200 },
  );
  if (refreshed || user.id !== resolved.userId || user.email !== resolved.email) {
    writeSessionCookies(response.cookies, {
      ...resolved,
      userId: user.id,
      email: user.email,
    });
  }
  return response;
}
