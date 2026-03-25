import { NextRequest, NextResponse } from "next/server";

import { getRequestOrigin, isValidAuthReturnPath } from "@/lib/authRedirect";
import {
  AUTH_RETURN_COOKIE,
  clearPkceCookies,
  clearSessionCookies,
  exchangeCodeForTokens,
  isAuthEnabled,
  PKCE_VERIFIER_COOKIE,
  writeSessionCookies,
} from "@/lib/entraAuth";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

function redirectToAuth(origin: string, reason: string): NextResponse {
  const url = new URL("/auth", origin);
  url.searchParams.set("reason", reason);
  const response = NextResponse.redirect(url);
  clearPkceCookies(response.cookies);
  clearSessionCookies(response.cookies);
  return response;
}

export async function GET(req: NextRequest): Promise<Response> {
  if (!isAuthEnabled()) {
    return NextResponse.json({ error: "Auth is disabled" }, { status: 400 });
  }

  const origin = getRequestOrigin(req);

  const oauthError = req.nextUrl.searchParams.get("error");
  if (oauthError) {
    return redirectToAuth(origin, "auth_failed");
  }

  const code = req.nextUrl.searchParams.get("code");
  if (!code) {
    return redirectToAuth(origin, "auth_failed");
  }

  const codeVerifier = req.cookies.get(PKCE_VERIFIER_COOKIE)?.value ?? "";
  if (!codeVerifier) {
    return redirectToAuth(origin, "auth_failed");
  }

  const returnRaw = req.cookies.get(AUTH_RETURN_COOKIE)?.value ?? "/dashboard";
  const returnPath = isValidAuthReturnPath(returnRaw) ? returnRaw.trim() : "/dashboard";

  const redirectUri = `${origin}/api/auth/callback`;

  try {
    const session = await exchangeCodeForTokens(code, codeVerifier, redirectUri);
    const target = new URL(returnPath, origin);
    if (target.origin !== origin) {
      return redirectToAuth(origin, "auth_failed");
    }
    const response = NextResponse.redirect(target);
    writeSessionCookies(response.cookies, session);
    clearPkceCookies(response.cookies);
    return response;
  } catch {
    return redirectToAuth(origin, "auth_failed");
  }
}
