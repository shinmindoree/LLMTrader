import { NextRequest, NextResponse } from "next/server";

import { auth } from "@/auth";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const RELAY_ORIGIN = process.env.RELAY_SERVER_URL ?? "";
const RELAY_API_KEY = process.env.RELAY_API_KEY ?? "";

export async function POST(req: NextRequest): Promise<Response> {
  if (!RELAY_ORIGIN) {
    return new Response(
      JSON.stringify({ error: "RELAY_SERVER_URL is not configured" }),
      { status: 500, headers: { "content-type": "application/json" } },
    );
  }

  const session = await auth();
  if (!session?.user) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const target = new URL("/generate", RELAY_ORIGIN);
  const body = await req.arrayBuffer();

  const headers = new Headers();
  headers.set("content-type", "application/json");
  if (RELAY_API_KEY) {
    headers.set("x-api-key", RELAY_API_KEY);
    headers.set("authorization", `Bearer ${RELAY_API_KEY}`);
  }

  const res = await fetch(target.toString(), {
    method: "POST",
    headers,
    body,
    redirect: "manual",
  });

  const resHeaders = new Headers(res.headers);
  resHeaders.delete("content-encoding");
  resHeaders.set("cache-control", "no-store");

  return new NextResponse(res.body, { status: res.status, headers: resHeaders });
}
