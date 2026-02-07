import { NextRequest, NextResponse } from "next/server";

import { isSupabaseAuthEnabled, readSessionCookies } from "@/lib/supabaseAuth";

const PUBLIC_FILE = /\.[^/]+$/;

function isPublicPath(pathname: string): boolean {
  if (pathname.startsWith("/_next")) return true;
  if (pathname.startsWith("/api/")) return true;
  if (pathname === "/favicon.ico") return true;
  if (pathname === "/auth") return true;
  return PUBLIC_FILE.test(pathname);
}

export function middleware(req: NextRequest): NextResponse {
  if (!isSupabaseAuthEnabled()) {
    return NextResponse.next();
  }

  const { pathname } = req.nextUrl;
  if (isPublicPath(pathname)) {
    const session = readSessionCookies(req.cookies);
    if (pathname === "/auth" && session) {
      const url = req.nextUrl.clone();
      url.pathname = "/";
      url.search = "";
      return NextResponse.redirect(url);
    }
    return NextResponse.next();
  }

  const session = readSessionCookies(req.cookies);
  if (session) {
    return NextResponse.next();
  }

  const url = req.nextUrl.clone();
  url.pathname = "/auth";
  url.search = "";
  return NextResponse.redirect(url);
}

export const config = {
  matcher: ["/:path*"],
};
