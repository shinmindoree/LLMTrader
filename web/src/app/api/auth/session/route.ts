import { NextRequest, NextResponse } from "next/server";

import { isAdminEmail } from "@/lib/admin";
import {
  clearSessionCookies,
  isAuthEnabled,
  readSessionCookies,
  refreshAccessToken,
  shouldRefreshSession,
  writeSessionCookies,
} from "@/lib/entraAuth";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(req: NextRequest): Promise<Response> {
  if (!isAuthEnabled()) {
    return NextResponse.json(
      { authenticated: false, isAdmin: false, reason: "disabled" },
      { status: 200 },
    );
  }

  const current = readSessionCookies(req.cookies);
  if (!current) {
    const response = NextResponse.json({ authenticated: false, isAdmin: false }, { status: 401 });
    clearSessionCookies(response.cookies);
    return response;
  }

  let resolved = current;
  let refreshed = false;

  if (shouldRefreshSession(current)) {
    const nextSession = await refreshAccessToken(current.refreshToken);
    if (!nextSession) {
      const response = NextResponse.json({ authenticated: false, isAdmin: false }, { status: 401 });
      clearSessionCookies(response.cookies);
      return response;
    }
    resolved = nextSession;
    refreshed = true;
  }

  const response = NextResponse.json(
    {
      authenticated: true,
      isAdmin: isAdminEmail(resolved.email),
      user: { id: resolved.userId, email: resolved.email },
    },
    { status: 200 },
  );
  if (refreshed) {
    writeSessionCookies(response.cookies, resolved);
  }
  return response;
}
