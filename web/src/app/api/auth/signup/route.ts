import { NextRequest, NextResponse } from "next/server";

import {
  isSupabaseAuthEnabled,
  signUpWithPassword,
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
  if (password.length < 8) {
    return NextResponse.json({ error: "password must be at least 8 characters" }, { status: 400 });
  }

  try {
    const result = await signUpWithPassword(email, password);
    const response = NextResponse.json(
      {
        ok: true,
        user: result.userId ? { id: result.userId, email: result.email } : null,
        needs_email_confirmation: result.session === null,
      },
      { status: 200 },
    );
    if (result.session) {
      writeSessionCookies(response.cookies, result.session);
    }
    return response;
  } catch (error) {
    return NextResponse.json(
      { error: error instanceof Error ? error.message : "Sign-up failed" },
      { status: 400 },
    );
  }
}
