import { NextRequest, NextResponse } from "next/server";

import {
  clearSessionCookies,
  isSupabaseAuthEnabled,
  signInWithPassword,
  writeSessionCookies,
} from "@/lib/supabaseAuth";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

function normalizeCredential(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

export async function POST(req: NextRequest): Promise<Response> {
  if (!isSupabaseAuthEnabled()) {
    return NextResponse.json(
      { error: "Supabase auth is disabled" },
      { status: 400 },
    );
  }

  let payload: unknown;
  try {
    payload = await req.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
  }

  const email = normalizeCredential((payload as Record<string, unknown>).email);
  const password = normalizeCredential((payload as Record<string, unknown>).password);
  if (!email || !password) {
    return NextResponse.json({ error: "email and password are required" }, { status: 400 });
  }

  try {
    const session = await signInWithPassword(email, password);
    const response = NextResponse.json(
      { ok: true, user: { id: session.userId, email: session.email } },
      { status: 200 },
    );
    writeSessionCookies(response.cookies, session);
    return response;
  } catch (error) {
    const response = NextResponse.json(
      { error: error instanceof Error ? error.message : "Login failed" },
      { status: 401 },
    );
    clearSessionCookies(response.cookies);
    return response;
  }
}
