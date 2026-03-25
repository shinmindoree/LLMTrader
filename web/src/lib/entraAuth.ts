/**
 * Microsoft Entra ID / External ID OIDC authentication helper.
 * Uses standard OIDC authorization code + PKCE flow.
 * Backend verifies JWT locally via JWKS (no egress to third-party auth service).
 *
 * Supports both:
 *  - Workforce tenant:  authority = https://login.microsoftonline.com/{tenantId}
 *  - External ID (CIAM): authority = https://{subdomain}.ciamlogin.com/{tenantId}
 */

type CookieValue = { value: string };

type ReadableCookieStore = {
  get(name: string): CookieValue | undefined;
};

type WritableCookieStore = {
  set(
    name: string,
    value: string,
    options?: {
      httpOnly?: boolean;
      sameSite?: "lax" | "strict" | "none";
      secure?: boolean;
      path?: string;
      maxAge?: number;
    },
  ): void;
  delete(name: string): void;
};

export type SessionSnapshot = {
  accessToken: string;
  idToken: string;
  refreshToken: string;
  expiresAt: number;
  userId: string;
  email: string | null;
};

// ── Cookie names ────────────────────────────────────────────
const ACCESS_TOKEN_COOKIE = "auth-access-token";
const ID_TOKEN_COOKIE = "auth-id-token";
const REFRESH_TOKEN_COOKIE = "auth-refresh-token";
const EXPIRES_AT_COOKIE = "auth-expires-at";
const USER_ID_COOKIE = "auth-user-id";
const USER_EMAIL_COOKIE = "auth-user-email";
export const PKCE_VERIFIER_COOKIE = "auth-pkce-verifier";
export const AUTH_RETURN_COOKIE = "auth-return";

const REFRESH_LEEWAY_SECONDS = 30;
const SESSION_COOKIE_MAX_AGE = 60 * 60 * 24 * 30; // 30 days
const PKCE_COOKIE_MAX_AGE = 60 * 10; // 10 minutes

// ── Helpers ─────────────────────────────────────────────────

function parseBoolean(value: string | undefined): boolean {
  const normalized = (value ?? "").trim().toLowerCase();
  return normalized === "1" || normalized === "true" || normalized === "yes" || normalized === "on";
}

export function isAuthEnabled(): boolean {
  return parseBoolean(
    process.env.NEXT_PUBLIC_ENTRA_AUTH_ENABLED ?? process.env.ENTRA_AUTH_ENABLED,
  );
}

type EntraConfig = {
  tenantId: string;
  clientId: string;
  clientSecret: string;
  authority: string;
};

function getEntraConfig(): EntraConfig {
  const tenantId = (
    process.env.NEXT_PUBLIC_ENTRA_TENANT_ID ?? process.env.ENTRA_TENANT_ID ?? ""
  ).trim();
  const clientId = (
    process.env.NEXT_PUBLIC_ENTRA_CLIENT_ID ?? process.env.ENTRA_CLIENT_ID ?? ""
  ).trim();
  const clientSecret = (process.env.ENTRA_CLIENT_SECRET ?? "").trim();
  const authorityRaw = (
    process.env.NEXT_PUBLIC_ENTRA_AUTHORITY ?? process.env.ENTRA_AUTHORITY ?? ""
  ).trim();

  if (!tenantId || !clientId) {
    throw new Error("Entra env is missing: ENTRA_TENANT_ID or ENTRA_CLIENT_ID");
  }

  const authority = authorityRaw || `https://login.microsoftonline.com/${tenantId}`;
  return { tenantId, clientId, clientSecret, authority: authority.replace(/\/+$/, "") };
}

export function sessionCookieOptions(maxAge: number): {
  httpOnly: boolean;
  sameSite: "lax";
  secure: boolean;
  path: "/";
  maxAge: number;
} {
  return {
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    path: "/",
    maxAge,
  };
}

// ── PKCE ────────────────────────────────────────────────────

function generatePkceVerifier(): string {
  const array = new Uint8Array(32);
  crypto.getRandomValues(array);
  return Array.from(array, (b) => b.toString(16).padStart(2, "0")).join("");
}

async function sha256Base64Url(input: string): Promise<string> {
  const data = new TextEncoder().encode(input);
  const hash = await crypto.subtle.digest("SHA-256", data);
  const bytes = new Uint8Array(hash);
  let binary = "";
  for (let i = 0; i < bytes.length; i++) {
    binary += String.fromCharCode(bytes[i]!);
  }
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

// ── Authorize URL ───────────────────────────────────────────

/**
 * Returns true when the authority URL points to an External ID (CIAM) tenant.
 */
export function isCiamAuthority(): boolean {
  try {
    const config = getEntraConfig();
    return config.authority.includes(".ciamlogin.com");
  } catch {
    return false;
  }
}

export async function createAuthorizeUrl(
  redirectUri: string,
  provider?: string,
): Promise<{ authorizeUrl: string; codeVerifier: string }> {
  const config = getEntraConfig();
  const codeVerifier = generatePkceVerifier();
  const codeChallenge = await sha256Base64Url(codeVerifier);

  const params = new URLSearchParams({
    client_id: config.clientId,
    response_type: "code",
    redirect_uri: redirectUri,
    scope: "openid profile email offline_access",
    code_challenge: codeChallenge,
    code_challenge_method: "S256",
    response_mode: "query",
  });

  // For External ID (CIAM): hint the identity provider (e.g. "google.com")
  if (provider && config.authority.includes(".ciamlogin.com")) {
    params.set("domain_hint", provider);
  }

  const authorizeUrl = `${config.authority}/oauth2/v2.0/authorize?${params.toString()}`;
  return { authorizeUrl, codeVerifier };
}

// ── Token Exchange ──────────────────────────────────────────

type TokenResponse = {
  access_token: string;
  id_token: string;
  refresh_token?: string;
  expires_in: number;
};

function decodeIdTokenPayload(
  idToken: string,
): { oid?: string; sub?: string; email?: string; preferred_username?: string } {
  const parts = idToken.split(".");
  if (parts.length !== 3) return {};
  const base64 = parts[1]!.replace(/-/g, "+").replace(/_/g, "/");
  const json = atob(base64);
  return JSON.parse(json);
}

function tokenResponseToSession(
  tokens: TokenResponse,
  fallbackRefreshToken?: string,
): SessionSnapshot {
  const idClaims = decodeIdTokenPayload(tokens.id_token || "");
  const userId = (idClaims.oid || idClaims.sub || "").trim();
  const email = (idClaims.email || idClaims.preferred_username || "").trim() || null;

  if (!userId) {
    throw new Error("ID token is missing user identifier (oid/sub)");
  }

  return {
    accessToken: tokens.access_token,
    idToken: tokens.id_token,
    refreshToken: tokens.refresh_token || fallbackRefreshToken || "",
    expiresAt: Math.floor(Date.now() / 1000) + tokens.expires_in,
    userId,
    email,
  };
}

export async function exchangeCodeForTokens(
  code: string,
  codeVerifier: string,
  redirectUri: string,
): Promise<SessionSnapshot> {
  const config = getEntraConfig();

  const body = new URLSearchParams({
    client_id: config.clientId,
    client_secret: config.clientSecret,
    code,
    redirect_uri: redirectUri,
    grant_type: "authorization_code",
    code_verifier: codeVerifier,
  });

  const response = await fetch(`${config.authority}/oauth2/v2.0/token`, {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: body.toString(),
    cache: "no-store",
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(`Token exchange failed: ${text}`);
  }

  const tokens = (await response.json()) as TokenResponse;
  return tokenResponseToSession(tokens);
}

// ── Token Refresh ───────────────────────────────────────────

export async function refreshAccessToken(
  refreshToken: string,
): Promise<SessionSnapshot | null> {
  const token = (refreshToken || "").trim();
  if (!token) return null;

  const config = getEntraConfig();

  const body = new URLSearchParams({
    client_id: config.clientId,
    client_secret: config.clientSecret,
    refresh_token: token,
    grant_type: "refresh_token",
    scope: "openid profile email offline_access",
  });

  const response = await fetch(`${config.authority}/oauth2/v2.0/token`, {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: body.toString(),
    cache: "no-store",
  });

  if (!response.ok) return null;

  const tokens = (await response.json()) as TokenResponse;
  return tokenResponseToSession(tokens, refreshToken);
}

// ── Cookie Management ───────────────────────────────────────

export function readSessionCookies(cookies: ReadableCookieStore): SessionSnapshot | null {
  const accessToken = cookies.get(ACCESS_TOKEN_COOKIE)?.value ?? "";
  const idToken = cookies.get(ID_TOKEN_COOKIE)?.value ?? "";
  const refreshToken = cookies.get(REFRESH_TOKEN_COOKIE)?.value ?? "";
  const expiresRaw = cookies.get(EXPIRES_AT_COOKIE)?.value ?? "";
  const userId = cookies.get(USER_ID_COOKIE)?.value ?? "";
  const expiresAt = Number.parseInt(expiresRaw, 10);

  if (!idToken || !userId || !Number.isFinite(expiresAt)) {
    return null;
  }

  return {
    accessToken,
    idToken,
    refreshToken,
    expiresAt,
    userId,
    email: cookies.get(USER_EMAIL_COOKIE)?.value || null,
  };
}

export function writeSessionCookies(cookies: WritableCookieStore, session: SessionSnapshot): void {
  const opts = sessionCookieOptions(SESSION_COOKIE_MAX_AGE);
  cookies.set(ACCESS_TOKEN_COOKIE, session.accessToken, opts);
  cookies.set(ID_TOKEN_COOKIE, session.idToken, opts);
  cookies.set(REFRESH_TOKEN_COOKIE, session.refreshToken, opts);
  cookies.set(EXPIRES_AT_COOKIE, String(session.expiresAt), opts);
  cookies.set(USER_ID_COOKIE, session.userId, opts);
  cookies.set(USER_EMAIL_COOKIE, session.email ?? "", opts);
}

export function clearSessionCookies(cookies: WritableCookieStore): void {
  cookies.delete(ACCESS_TOKEN_COOKIE);
  cookies.delete(ID_TOKEN_COOKIE);
  cookies.delete(REFRESH_TOKEN_COOKIE);
  cookies.delete(EXPIRES_AT_COOKIE);
  cookies.delete(USER_ID_COOKIE);
  cookies.delete(USER_EMAIL_COOKIE);
}

export function shouldRefreshSession(session: SessionSnapshot): boolean {
  const now = Math.floor(Date.now() / 1000);
  return session.expiresAt <= now + REFRESH_LEEWAY_SECONDS;
}

export function writePkceCookies(
  cookies: WritableCookieStore,
  codeVerifier: string,
  returnPath: string,
): void {
  const opts = sessionCookieOptions(PKCE_COOKIE_MAX_AGE);
  cookies.set(PKCE_VERIFIER_COOKIE, codeVerifier, opts);
  cookies.set(AUTH_RETURN_COOKIE, returnPath, opts);
}

export function clearPkceCookies(cookies: WritableCookieStore): void {
  cookies.delete(PKCE_VERIFIER_COOKIE);
  cookies.delete(AUTH_RETURN_COOKIE);
}
