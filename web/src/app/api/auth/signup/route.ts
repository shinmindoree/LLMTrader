import { NextRequest, NextResponse } from "next/server";

import { getRequestOrigin, isValidAuthReturnPath } from "@/lib/authRedirect";
import {
  clearPkceCookies,
  createAuthorizeUrl,
  isAuthEnabled,
  writePkceCookies,
} from "@/lib/entraAuth";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(req: NextRequest): Promise<Response> {
  if (!isAuthEnabled()) {
    return NextResponse.json({ error: "Auth is disabled" }, { status: 400 });
  }

  const returnParam = req.nextUrl.searchParams.get("returnUrl") ?? "";
  const returnPath =
    returnParam && isValidAuthReturnPath(returnParam) ? returnParam.trim() : "/dashboard";

  const origin = getRequestOrigin(req);
  const redirectUri = `${origin}/api/auth/callback`;

  try {
    const { authorizeUrl, codeVerifier } = await createAuthorizeUrl(redirectUri);
    const response = NextResponse.redirect(authorizeUrl);
    clearPkceCookies(response.cookies);
    writePkceCookies(response.cookies, codeVerifier, returnPath);
    return response;
  } catch (error) {
    return NextResponse.json(
      { error: error instanceof Error ? error.message : "Signup redirect failed" },
      { status: 500 },
    );
  }
}
