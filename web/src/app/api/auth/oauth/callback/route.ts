import { NextRequest, NextResponse } from "next/server";

// Legacy route — NextAuth handles OAuth callbacks via /api/auth/[...nextauth]
export async function GET(req: NextRequest): Promise<Response> {
  const url = new URL("/auth", req.url);
  return NextResponse.redirect(url);
}
