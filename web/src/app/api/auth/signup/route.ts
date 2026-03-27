import { NextRequest, NextResponse } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const API_ORIGIN = process.env.API_ORIGIN ?? "http://localhost:8000";

export async function POST(req: NextRequest): Promise<Response> {
  try {
    const body = await req.json();
    const res = await fetch(`${API_ORIGIN}/api/auth/register`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if (!res.ok) {
      return NextResponse.json(
        { error: data.detail ?? "Registration failed" },
        { status: res.status },
      );
    }
    return NextResponse.json(data);
  } catch {
    return NextResponse.json(
      { error: "Registration service unavailable" },
      { status: 502 },
    );
  }
}

// Legacy route — redirect to auth page
export async function GET(req: NextRequest): Promise<Response> {
  const url = new URL("/auth", req.url);
  return NextResponse.redirect(url);
}
