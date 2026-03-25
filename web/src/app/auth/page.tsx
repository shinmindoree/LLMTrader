"use client";

import { useSearchParams } from "next/navigation";
import { isValidAuthReturnPath } from "@/lib/authRedirect";
import { useI18n } from "@/lib/i18n";

function isAuthEnabled(): boolean {
  const raw = (process.env.NEXT_PUBLIC_ENTRA_AUTH_ENABLED ?? "").trim().toLowerCase();
  return raw === "1" || raw === "true" || raw === "yes" || raw === "on";
}

function isCiamEnabled(): boolean {
  const authority = (process.env.NEXT_PUBLIC_ENTRA_AUTHORITY ?? "").trim();
  return authority.includes(".ciamlogin.com");
}

export default function AuthPage() {
  const { t } = useI18n();
  const searchParams = useSearchParams();
  const returnUrl = searchParams.get("returnUrl");
  const reason = searchParams.get("reason");
  const showSessionExpired = reason === "session_expired";
  const showAuthFailed = reason === "auth_failed" || reason === "oauth_failed";

  if (!isAuthEnabled()) {
    return (
      <main className="mx-auto w-full max-w-md px-6 py-16">
        <div className="rounded-lg border border-[#2a2e39] bg-[#1e222d] p-6">
          <h1 className="text-lg font-semibold text-[#d1d4dc]">{t.authDisabled.title}</h1>
          <p className="mt-2 text-sm text-[#868993]">
            {t.authDisabled.description}
          </p>
        </div>
      </main>
    );
  }

  const loginHref = `/api/auth/login${
    returnUrl && isValidAuthReturnPath(returnUrl)
      ? `?returnUrl=${encodeURIComponent(returnUrl)}`
      : ""
  }`;

  return (
    <main className="mx-auto w-full max-w-md px-6 py-16">
      <div className="rounded-lg border border-[#2a2e39] bg-[#1e222d] p-6">
        <h1 className="text-lg font-semibold text-[#d1d4dc]">
          {t.auth.login}
        </h1>
        <p className="mt-2 text-sm text-[#868993]">
          {t.auth.description}
        </p>

        {showSessionExpired ? (
          <p className="mt-3 rounded border border-[#2962ff]/40 bg-[#1a2744]/50 px-3 py-2 text-sm text-[#7eb8ff]">
            {t.auth.sessionExpired}
          </p>
        ) : null}
        {showAuthFailed ? (
          <p className="mt-3 rounded border border-[#ef5350]/30 bg-[#2d1f1f]/50 px-3 py-2 text-sm text-[#ef5350]">
            {t.auth.authFailed}
          </p>
        ) : null}

        <a
          className="mt-6 flex w-full items-center justify-center gap-2 rounded bg-[#2962ff] px-3 py-2.5 text-sm font-medium text-white transition-colors hover:bg-[#2962ff]/90"
          href={loginHref}
        >
          <MicrosoftGlyph />
          {t.auth.login}
        </a>

        {isCiamEnabled() ? (
          <>
            <div className="my-4 flex items-center gap-3">
              <div className="h-px flex-1 bg-[#2a2e39]" />
              <span className="text-xs text-[#868993]">or</span>
              <div className="h-px flex-1 bg-[#2a2e39]" />
            </div>
            <a
              className="flex w-full items-center justify-center gap-2 rounded border border-[#2a2e39] bg-[#1e222d] px-3 py-2.5 text-sm font-medium text-[#d1d4dc] transition-colors hover:bg-[#262a35]"
              href={`${loginHref}${loginHref.includes("?") ? "&" : "?"}provider=google.com`}
            >
              <GoogleGlyph />
              {t.auth.continueWithGoogle}
            </a>
          </>
        ) : null}
      </div>
    </main>
  );
}

function MicrosoftGlyph() {
  return (
    <svg aria-hidden className="h-5 w-5 shrink-0" viewBox="0 0 21 21">
      <rect x="1" y="1" width="9" height="9" fill="#f25022" />
      <rect x="11" y="1" width="9" height="9" fill="#7fba00" />
      <rect x="1" y="11" width="9" height="9" fill="#00a4ef" />
      <rect x="11" y="11" width="9" height="9" fill="#ffb900" />
    </svg>
  );
}

function GoogleGlyph() {
  return (
    <svg aria-hidden className="h-5 w-5 shrink-0" viewBox="0 0 24 24">
      <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 0 1-2.2 3.32v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.1z" fill="#4285F4" />
      <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853" />
      <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" fill="#FBBC05" />
      <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335" />
    </svg>
  );
}
