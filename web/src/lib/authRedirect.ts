import type { NextRequest } from "next/server";

export function getRequestOrigin(req: NextRequest): string {
  const host = req.headers.get("x-forwarded-host") ?? req.headers.get("host") ?? "localhost:3000";
  const forwardedProto = req.headers.get("x-forwarded-proto");
  const proto =
    forwardedProto?.split(",")[0]?.trim() ??
    (process.env.NODE_ENV === "production" ? "https" : "http");
  return `${proto}://${host}`;
}

export function isValidAuthReturnPath(pathname: string): boolean {
  const trimmed = pathname.trim();
  return trimmed.startsWith("/") && !trimmed.startsWith("//") && !trimmed.includes(":");
}
