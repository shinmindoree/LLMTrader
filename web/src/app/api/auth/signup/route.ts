import { NextRequest, NextResponse } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// Legacy route — redirect to NextAuth sign-in page
export async function GET(req: NextRequest): Promise<Response> {
  const url = new URL("/auth", req.url);
  return NextResponse.redirect(url);
}
