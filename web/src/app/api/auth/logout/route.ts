import { NextRequest, NextResponse } from "next/server";

import {
  clearSessionCookies,
  isAuthEnabled,
  readSessionCookies,
} from "@/lib/entraAuth";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(req: NextRequest): Promise<Response> {
  const response = NextResponse.json({ ok: true }, { status: 200 });
  clearSessionCookies(response.cookies);
  return response;
}
