import { NextRequest, NextResponse } from "next/server";

import { getRequestOrigin, isValidAuthReturnPath } from "@/lib/authRedirect";
import {
  clearOAuthStartCookies,
  createGoogleOAuthAuthorizeUrl,
  isSupabaseAuthEnabled,
  writeOAuthStartCookies,
} from "@/lib/supabaseAuth";

export async function GET(req: NextRequest): Promise<Response> {
  if (!isSupabaseAuthEnabled()) {
    return NextResponse.json({ error: "Supabase auth is disabled" }, { status: 400 });
  }

  const returnParam = req.nextUrl.searchParams.get("returnUrl") ?? "";
  const returnPath =
    returnParam && isValidAuthReturnPath(returnParam) ? returnParam.trim() : "/dashboard";

  const origin = getRequestOrigin(req);
  const callbackUrl = `${origin}/api/auth/oauth/callback`;

  try {
    const { authorizeUrl, codeVerifier } = await createGoogleOAuthAuthorizeUrl(callbackUrl);
    const response = NextResponse.redirect(authorizeUrl);
    clearOAuthStartCookies(response.cookies);
    writeOAuthStartCookies(response.cookies, codeVerifier, returnPath);
    return response;
  } catch (error) {
    return NextResponse.json(
      { error: error instanceof Error ? error.message : "OAuth start failed" },
      { status: 500 },
    );
  }
}
