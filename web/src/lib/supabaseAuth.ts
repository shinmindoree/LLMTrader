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

type SupabaseUserPayload = {
  id?: string;
  email?: string | null;
};

type SupabaseTokenPayload = {
  access_token?: string;
  refresh_token?: string;
  expires_in?: number;
  expires_at?: number;
  user?: SupabaseUserPayload | null;
};

export type SessionSnapshot = {
  accessToken: string;
  refreshToken: string;
  expiresAt: number;
  userId: string;
  email: string | null;
};

const ACCESS_TOKEN_COOKIE = "sb-access-token";
const REFRESH_TOKEN_COOKIE = "sb-refresh-token";
const EXPIRES_AT_COOKIE = "sb-expires-at";
const USER_ID_COOKIE = "sb-user-id";
const USER_EMAIL_COOKIE = "sb-user-email";
const REFRESH_LEEWAY_SECONDS = 30;
const REFRESH_TOKEN_MAX_AGE = 60 * 60 * 24 * 30;
export const OAUTH_PKCE_COOKIE = "sb-oauth-pkce";
export const OAUTH_RETURN_COOKIE = "sb-oauth-return";
const OAUTH_PKCE_MAX_AGE = 60 * 10;

function parseBoolean(value: string | undefined): boolean {
  const normalized = (value ?? "").trim().toLowerCase();
  return normalized === "1" || normalized === "true" || normalized === "yes" || normalized === "on";
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

function cookieOptions(maxAge: number): ReturnType<typeof sessionCookieOptions> {
  return sessionCookieOptions(maxAge);
}

function ensureSupabaseConfig(): { url: string; anonKey: string } {
  const url = (
    process.env.NEXT_PUBLIC_SUPABASE_URL
    ?? process.env.SUPABASE_URL
    ?? ""
  ).trim();
  const anonKey = (
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY
    ?? process.env.SUPABASE_ANON_KEY
    ?? ""
  ).trim();
  if (!url || !anonKey) {
    throw new Error("Supabase env is missing: NEXT_PUBLIC_SUPABASE_URL/NEXT_PUBLIC_SUPABASE_ANON_KEY");
  }
  return { url: url.replace(/\/+$/, ""), anonKey };
}

async function supabaseRequest(path: string, init?: RequestInit): Promise<Response> {
  const { url, anonKey } = ensureSupabaseConfig();
  const headers = new Headers(init?.headers ?? {});
  headers.set("apikey", anonKey);
  if (!headers.has("content-type") && init?.body !== undefined) {
    headers.set("content-type", "application/json");
  }
  return fetch(`${url}${path}`, {
    ...init,
    headers,
    cache: "no-store",
  });
}

async function parseErrorMessage(response: Response): Promise<string> {
  try {
    const payload = await response.json();
    if (payload && typeof payload === "object") {
      const asRecord = payload as Record<string, unknown>;
      const message = asRecord.msg ?? asRecord.message ?? asRecord.error_description ?? asRecord.error;
      if (typeof message === "string" && message.trim()) {
        return message.trim();
      }
    }
  } catch {
    // ignore parse error and fallback to status text
  }
  return response.statusText || "Supabase request failed";
}

function normalizeSessionFromPayload(
  payload: SupabaseTokenPayload,
  fallbackUserId = "",
  fallbackEmail: string | null = null,
): SessionSnapshot | null {
  const accessToken = (payload.access_token ?? "").trim();
  const refreshToken = (payload.refresh_token ?? "").trim();
  if (!accessToken || !refreshToken) {
    return null;
  }

  const expiresAt =
    typeof payload.expires_at === "number"
      ? Math.floor(payload.expires_at)
      : Math.floor(Date.now() / 1000) + Math.max(60, Number(payload.expires_in ?? 3600));
  const userId = (payload.user?.id ?? fallbackUserId ?? "").trim();
  const emailValue = payload.user?.email;
  const email = typeof emailValue === "string" ? emailValue : fallbackEmail;
  if (!userId) {
    return null;
  }

  return {
    accessToken,
    refreshToken,
    expiresAt,
    userId,
    email: email ?? null,
  };
}

export function isSupabaseAuthEnabled(): boolean {
  return parseBoolean(
    process.env.NEXT_PUBLIC_SUPABASE_AUTH_ENABLED
    ?? process.env.SUPABASE_AUTH_ENABLED,
  );
}

export function readSessionCookies(cookies: ReadableCookieStore): SessionSnapshot | null {
  const accessToken = cookies.get(ACCESS_TOKEN_COOKIE)?.value ?? "";
  const refreshToken = cookies.get(REFRESH_TOKEN_COOKIE)?.value ?? "";
  const userId = cookies.get(USER_ID_COOKIE)?.value ?? "";
  const expiresRaw = cookies.get(EXPIRES_AT_COOKIE)?.value ?? "";
  const emailRaw = cookies.get(USER_EMAIL_COOKIE)?.value ?? "";
  const expiresAt = Number.parseInt(expiresRaw, 10);

  if (!accessToken || !refreshToken || !userId || !Number.isFinite(expiresAt)) {
    return null;
  }
  return {
    accessToken,
    refreshToken,
    expiresAt,
    userId,
    email: emailRaw || null,
  };
}

export function shouldRefreshSession(session: SessionSnapshot): boolean {
  const now = Math.floor(Date.now() / 1000);
  return session.expiresAt <= now + REFRESH_LEEWAY_SECONDS;
}

export function clearSessionCookies(cookies: WritableCookieStore): void {
  cookies.delete(ACCESS_TOKEN_COOKIE);
  cookies.delete(REFRESH_TOKEN_COOKIE);
  cookies.delete(EXPIRES_AT_COOKIE);
  cookies.delete(USER_ID_COOKIE);
  cookies.delete(USER_EMAIL_COOKIE);
}

export function writeSessionCookies(cookies: WritableCookieStore, session: SessionSnapshot): void {
  cookies.set(ACCESS_TOKEN_COOKIE, session.accessToken, cookieOptions(REFRESH_TOKEN_MAX_AGE));
  cookies.set(REFRESH_TOKEN_COOKIE, session.refreshToken, cookieOptions(REFRESH_TOKEN_MAX_AGE));
  cookies.set(EXPIRES_AT_COOKIE, String(session.expiresAt), cookieOptions(REFRESH_TOKEN_MAX_AGE));
  cookies.set(USER_ID_COOKIE, session.userId, cookieOptions(REFRESH_TOKEN_MAX_AGE));
  cookies.set(USER_EMAIL_COOKIE, session.email ?? "", cookieOptions(REFRESH_TOKEN_MAX_AGE));
}

export async function signUpWithPassword(email: string, password: string): Promise<{
  userId: string | null;
  email: string | null;
  session: SessionSnapshot | null;
}> {
  const response = await supabaseRequest("/auth/v1/signup", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
  if (!response.ok) {
    const message = await parseErrorMessage(response);
    throw new Error(message);
  }

  const payload = (await response.json()) as {
    user?: SupabaseUserPayload | null;
    session?: SupabaseTokenPayload | null;
  };

  const userId = (payload.user?.id ?? "").trim() || null;
  const userEmail = typeof payload.user?.email === "string" ? payload.user.email : null;
  const session = payload.session
    ? normalizeSessionFromPayload(payload.session, userId ?? "", userEmail)
    : null;

  return { userId, email: userEmail, session };
}

function generateOAuthPkceVerifier(): string {
  const verifierLength = 56;
  const array = new Uint32Array(verifierLength);
  crypto.getRandomValues(array);
  return Array.from(array, (dec) => ("0" + dec.toString(16)).slice(-2)).join("");
}

async function sha256Base64Url(input: string): Promise<string> {
  const data = new TextEncoder().encode(input);
  const hash = await crypto.subtle.digest("SHA-256", data);
  const bytes = new Uint8Array(hash);
  let binary = "";
  for (let i = 0; i < bytes.length; i++) {
    binary += String.fromCharCode(bytes[i]!);
  }
  const b64 = btoa(binary);
  return b64.replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export async function createGoogleOAuthAuthorizeUrl(
  supabaseCallbackUrl: string,
): Promise<{ authorizeUrl: string; codeVerifier: string }> {
  const { url } = ensureSupabaseConfig();
  const codeVerifier = generateOAuthPkceVerifier();
  const codeChallenge = await sha256Base64Url(codeVerifier);
  const params = new URLSearchParams({
    provider: "google",
    redirect_to: supabaseCallbackUrl,
    code_challenge: codeChallenge,
    code_challenge_method: "s256",
  });
  const authorizeUrl = `${url}/auth/v1/authorize?${params.toString()}`;
  return { authorizeUrl, codeVerifier };
}

export async function exchangeOAuthPkceCode(
  authCode: string,
  codeVerifier: string,
): Promise<SessionSnapshot> {
  const code = (authCode || "").trim();
  const verifier = (codeVerifier || "").trim();
  if (!code || !verifier) {
    throw new Error("Missing OAuth code or verifier");
  }
  const response = await supabaseRequest("/auth/v1/token?grant_type=pkce", {
    method: "POST",
    body: JSON.stringify({ auth_code: code, code_verifier: verifier }),
  });
  if (!response.ok) {
    const message = await parseErrorMessage(response);
    throw new Error(message);
  }
  const payload = (await response.json()) as SupabaseTokenPayload;
  const session = normalizeSessionFromPayload(payload);
  if (!session) {
    throw new Error("Supabase OAuth response is missing session data");
  }
  return session;
}

export function writeOAuthStartCookies(
  cookies: WritableCookieStore,
  codeVerifier: string,
  returnPath: string,
): void {
  cookies.set(OAUTH_PKCE_COOKIE, codeVerifier, sessionCookieOptions(OAUTH_PKCE_MAX_AGE));
  cookies.set(OAUTH_RETURN_COOKIE, returnPath, sessionCookieOptions(OAUTH_PKCE_MAX_AGE));
}

export function clearOAuthStartCookies(cookies: WritableCookieStore): void {
  cookies.delete(OAUTH_PKCE_COOKIE);
  cookies.delete(OAUTH_RETURN_COOKIE);
}

export async function signInWithPassword(email: string, password: string): Promise<SessionSnapshot> {
  const response = await supabaseRequest("/auth/v1/token?grant_type=password", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
  if (!response.ok) {
    const message = await parseErrorMessage(response);
    throw new Error(message);
  }
  const payload = (await response.json()) as SupabaseTokenPayload;
  const session = normalizeSessionFromPayload(payload);
  if (!session) {
    throw new Error("Supabase login response is missing session data");
  }
  return session;
}

export async function refreshSession(
  refreshToken: string,
  fallbackUserId = "",
  fallbackEmail: string | null = null,
): Promise<SessionSnapshot | null> {
  const token = (refreshToken || "").trim();
  if (!token) {
    return null;
  }
  const response = await supabaseRequest("/auth/v1/token?grant_type=refresh_token", {
    method: "POST",
    body: JSON.stringify({ refresh_token: token }),
  });
  if (!response.ok) {
    return null;
  }
  const payload = (await response.json()) as SupabaseTokenPayload;
  return normalizeSessionFromPayload(payload, fallbackUserId, fallbackEmail);
}

export async function fetchSupabaseUser(accessToken: string): Promise<{ id: string; email: string | null } | null> {
  const token = (accessToken || "").trim();
  if (!token) {
    return null;
  }
  const response = await supabaseRequest("/auth/v1/user", {
    method: "GET",
    headers: { authorization: `Bearer ${token}` },
  });
  if (!response.ok) {
    return null;
  }
  const payload = (await response.json()) as SupabaseUserPayload;
  const userId = (payload.id ?? "").trim();
  if (!userId) {
    return null;
  }
  const email = typeof payload.email === "string" ? payload.email : null;
  return { id: userId, email };
}

export async function logoutSupabaseSession(accessToken: string): Promise<void> {
  const token = (accessToken || "").trim();
  if (!token) {
    return;
  }
  await supabaseRequest("/auth/v1/logout", {
    method: "POST",
    headers: { authorization: `Bearer ${token}` },
  });
}
