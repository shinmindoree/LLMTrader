"use client";

import { useState, type FormEvent } from "react";
import { useSearchParams } from "next/navigation";
import { signIn } from "next-auth/react";
import { isValidAuthReturnPath } from "@/lib/authRedirect";
import { useI18n } from "@/lib/i18n";

export default function AuthPage() {
  const { t } = useI18n();
  const searchParams = useSearchParams();
  const returnUrl = searchParams.get("returnUrl") ?? searchParams.get("callbackUrl");
  const reason = searchParams.get("reason");
  const errorParam = searchParams.get("error");
  const showSessionExpired = reason === "session_expired";
  const showAuthFailed = reason === "auth_failed" || reason === "oauth_failed" || !!errorParam;

  const callbackUrl = returnUrl && isValidAuthReturnPath(returnUrl) ? returnUrl : "/";

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [credError, setCredError] = useState("");

  async function handleCredentialsSubmit(e: FormEvent) {
    e.preventDefault();
    setCredError("");
    setSubmitting(true);
    try {
      const result = await signIn("credentials", {
        email,
        password,
        callbackUrl,
        redirect: false,
      });
      if (result?.error) {
        setCredError(t.auth.authFailed);
      } else if (result?.url) {
        window.location.href = result.url;
      }
    } finally {
      setSubmitting(false);
    }
  }

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

        {/* Google Sign-In */}
        <button
          type="button"
          className="mt-6 flex w-full items-center justify-center gap-2 rounded border border-[#2a2e39] bg-[#1e222d] px-3 py-2.5 text-sm font-medium text-[#d1d4dc] transition-colors hover:bg-[#262a35]"
          onClick={() => signIn("google", { callbackUrl })}
        >
          <GoogleGlyph />
          {t.auth.continueWithGoogle}
        </button>

        {/* Divider */}
        <div className="my-4 flex items-center gap-3">
          <div className="h-px flex-1 bg-[#2a2e39]" />
          <span className="text-xs text-[#868993]">{t.auth.orUseEmail}</span>
          <div className="h-px flex-1 bg-[#2a2e39]" />
        </div>

        {/* Credentials Form */}
        <form onSubmit={handleCredentialsSubmit} className="space-y-3">
          {credError ? (
            <p className="rounded border border-[#ef5350]/30 bg-[#2d1f1f]/50 px-3 py-2 text-sm text-[#ef5350]">
              {credError}
            </p>
          ) : null}
          <input
            type="email"
            required
            placeholder="Email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="w-full rounded border border-[#2a2e39] bg-[#131722] px-3 py-2 text-sm text-[#d1d4dc] placeholder-[#868993] outline-none focus:border-[#2962ff]"
          />
          <input
            type="password"
            required
            placeholder="Password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="w-full rounded border border-[#2a2e39] bg-[#131722] px-3 py-2 text-sm text-[#d1d4dc] placeholder-[#868993] outline-none focus:border-[#2962ff]"
          />
          <button
            type="submit"
            disabled={submitting}
            className="w-full rounded bg-[#2962ff] px-3 py-2.5 text-sm font-medium text-white transition-colors hover:bg-[#2962ff]/90 disabled:opacity-60"
          >
            {submitting ? t.auth.submitting : t.auth.login}
          </button>
        </form>
      </div>
    </main>
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
