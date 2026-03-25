import { NextRequest, NextResponse } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// Legacy route — NextAuth handles callbacks via /api/auth/[...nextauth]
export async function GET(req: NextRequest): Promise<Response> {
  const url = new URL("/auth", req.url);
  return NextResponse.redirect(url);
}
