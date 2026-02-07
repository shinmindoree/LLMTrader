import { NextRequest, NextResponse } from "next/server";

import {
  clearSessionCookies,
  isSupabaseAuthEnabled,
  logoutSupabaseSession,
  readSessionCookies,
} from "@/lib/supabaseAuth";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(req: NextRequest): Promise<Response> {
  const response = NextResponse.json({ ok: true }, { status: 200 });

  if (!isSupabaseAuthEnabled()) {
    clearSessionCookies(response.cookies);
    return response;
  }

  const session = readSessionCookies(req.cookies);
  if (session) {
    try {
      await logoutSupabaseSession(session.accessToken);
    } catch {
      // ignore logout call failure and clear local cookies
    }
  }

  clearSessionCookies(response.cookies);
  return response;
}
