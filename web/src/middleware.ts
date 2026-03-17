import { NextRequest, NextResponse } from "next/server";

import { isAdminEmail } from "@/lib/admin";
import { isSupabaseAuthEnabled, readSessionCookies } from "@/lib/supabaseAuth";

const PUBLIC_FILE = /\.[^/]+$/;

function isPublicPath(pathname: string): boolean {
  if (pathname.startsWith("/_next")) return true;
  if (pathname.startsWith("/api/")) return true;
  if (pathname === "/favicon.ico") return true;
  if (pathname === "/auth") return true;
  if (pathname === "/") return true;
  return PUBLIC_FILE.test(pathname);
}

export function middleware(req: NextRequest): NextResponse {
  const { pathname } = req.nextUrl;

  if ((pathname === "/admin" || pathname.startsWith("/admin/")) && !isSupabaseAuthEnabled()) {
    const url = req.nextUrl.clone();
    url.pathname = "/dashboard";
    url.search = "";
    return NextResponse.redirect(url);
  }

  if (!isSupabaseAuthEnabled()) {
    return NextResponse.next();
  }

  if (isPublicPath(pathname)) {
    const session = readSessionCookies(req.cookies);
    if (pathname === "/auth" && session) {
      const url = req.nextUrl.clone();
      url.pathname = "/dashboard";
      url.search = "";
      return NextResponse.redirect(url);
    }
    return NextResponse.next();
  }

  const session = readSessionCookies(req.cookies);
  if (session) {
    if (pathname === "/admin" || pathname.startsWith("/admin/")) {
      if (!isAdminEmail(session.email)) {
        const url = req.nextUrl.clone();
        url.pathname = "/dashboard";
        url.search = "";
        return NextResponse.redirect(url);
      }
    }
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
