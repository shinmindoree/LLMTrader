import { NextRequest, NextResponse } from "next/server";

import { getRequestOrigin, isValidAuthReturnPath } from "@/lib/authRedirect";
import {
  clearOAuthStartCookies,
  clearSessionCookies,
  exchangeOAuthPkceCode,
  isSupabaseAuthEnabled,
  OAUTH_PKCE_COOKIE,
  OAUTH_RETURN_COOKIE,
  writeSessionCookies,
} from "@/lib/supabaseAuth";

function redirectToAuth(origin: string, reason: string): NextResponse {
  const url = new URL("/auth", origin);
  url.searchParams.set("reason", reason);
  const response = NextResponse.redirect(url);
  clearOAuthStartCookies(response.cookies);
  clearSessionCookies(response.cookies);
  return response;
}

export async function GET(req: NextRequest): Promise<Response> {
  if (!isSupabaseAuthEnabled()) {
    return NextResponse.json({ error: "Supabase auth is disabled" }, { status: 400 });
  }

  const origin = getRequestOrigin(req);
  const oauthError = req.nextUrl.searchParams.get("error");
  if (oauthError) {
    return redirectToAuth(origin, "oauth_failed");
  }

  const code = req.nextUrl.searchParams.get("code");
  if (!code) {
    return redirectToAuth(origin, "oauth_failed");
  }

  const codeVerifier = req.cookies.get(OAUTH_PKCE_COOKIE)?.value ?? "";
  if (!codeVerifier) {
    return redirectToAuth(origin, "oauth_failed");
  }

  const returnRaw = req.cookies.get(OAUTH_RETURN_COOKIE)?.value ?? "/dashboard";
  const returnPath = isValidAuthReturnPath(returnRaw) ? returnRaw.trim() : "/dashboard";

  try {
    const session = await exchangeOAuthPkceCode(code, codeVerifier);
    const target = new URL(returnPath, origin);
    if (target.origin !== origin) {
      return redirectToAuth(origin, "oauth_failed");
    }
    const response = NextResponse.redirect(target);
    writeSessionCookies(response.cookies, session);
    clearOAuthStartCookies(response.cookies);
    return response;
  } catch {
    return redirectToAuth(origin, "oauth_failed");
  }
}
