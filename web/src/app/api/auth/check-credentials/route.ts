import { NextRequest, NextResponse } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const API_ORIGIN = process.env.API_ORIGIN ?? "http://localhost:8000";

/**
 * Pre-check credentials before NextAuth signIn.
 * Returns { ok, reason } so the frontend can show appropriate UI
 * (e.g. email-not-verified screen) instead of a generic error.
 */
export async function POST(req: NextRequest): Promise<Response> {
  try {
    const body = await req.json();
    const res = await fetch(`${API_ORIGIN}/api/auth/verify-credentials`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });

    if (res.ok) {
      return NextResponse.json({ ok: true });
    }

    if (res.status === 403) {
      return NextResponse.json({ ok: false, reason: "EMAIL_NOT_VERIFIED" }, { status: 403 });
    }

    if (res.status === 401) {
      return NextResponse.json({ ok: false, reason: "INVALID_CREDENTIALS" }, { status: 401 });
    }

    return NextResponse.json({ ok: false, reason: "UNKNOWN" }, { status: res.status });
  } catch {
    return NextResponse.json({ ok: false, reason: "SERVICE_UNAVAILABLE" }, { status: 502 });
  }
}
