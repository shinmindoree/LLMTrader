"use client";

import { useSearchParams } from "next/navigation";
import { isValidAuthReturnPath } from "@/lib/authRedirect";
import { useI18n } from "@/lib/i18n";

function isAuthEnabled(): boolean {
  const raw = (process.env.NEXT_PUBLIC_ENTRA_AUTH_ENABLED ?? "").trim().toLowerCase();
  return raw === "1" || raw === "true" || raw === "yes" || raw === "on";
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
