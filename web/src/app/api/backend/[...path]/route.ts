import { NextRequest } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const API_ORIGIN = process.env.API_ORIGIN ?? "http://localhost:8000";
const ADMIN_TOKEN = process.env.ADMIN_TOKEN ?? "dev-admin-token";

async function proxy(req: NextRequest, params: { path: string[] }): Promise<Response> {
  const url = new URL(req.url);
  const relPath = params.path.join("/");
  const target = new URL(relPath, API_ORIGIN.endsWith("/") ? API_ORIGIN : `${API_ORIGIN}/`);
  target.search = url.search;

  const headers = new Headers(req.headers);
  headers.set("x-admin-token", ADMIN_TOKEN);
  headers.delete("host");

  let body: ArrayBuffer | undefined;
  if (req.method !== "GET" && req.method !== "HEAD") {
    body = await req.arrayBuffer();
  }

  const res = await fetch(target.toString(), {
    method: req.method,
    headers,
    body,
    redirect: "manual",
  });

  const resHeaders = new Headers(res.headers);
  resHeaders.delete("content-encoding");
  resHeaders.set("cache-control", "no-store");

  return new Response(res.body, { status: res.status, headers: resHeaders });
}

type RouteCtx = { params: Promise<{ path: string[] }> };

export async function GET(req: NextRequest, ctx: RouteCtx): Promise<Response> {
  const params = await ctx.params;
  return proxy(req, params);
}

export async function POST(req: NextRequest, ctx: RouteCtx): Promise<Response> {
  const params = await ctx.params;
  return proxy(req, params);
}

export async function PUT(req: NextRequest, ctx: RouteCtx): Promise<Response> {
  const params = await ctx.params;
  return proxy(req, params);
}

export async function PATCH(req: NextRequest, ctx: RouteCtx): Promise<Response> {
  const params = await ctx.params;
  return proxy(req, params);
}

export async function DELETE(req: NextRequest, ctx: RouteCtx): Promise<Response> {
  const params = await ctx.params;
  return proxy(req, params);
}
