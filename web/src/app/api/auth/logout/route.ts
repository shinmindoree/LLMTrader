import { NextResponse } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(): Promise<Response> {
  // NextAuth handles sign-out via /api/auth/signout
  // This endpoint is kept for backward compat with UserProfileMenu
  return NextResponse.json({ ok: true }, { status: 200 });
}
