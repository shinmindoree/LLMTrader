"use client";

import { useSearchParams } from "next/navigation";
import { useState } from "react";
import { isValidAuthReturnPath } from "@/lib/authRedirect";
import { useI18n } from "@/lib/i18n";

type AuthMode = "login" | "signup";

function isAuthEnabled(): boolean {
  const raw = (process.env.NEXT_PUBLIC_SUPABASE_AUTH_ENABLED ?? "").trim().toLowerCase();
  return raw === "1" || raw === "true" || raw === "yes" || raw === "on";
}

export default function AuthPage() {
  const { t } = useI18n();
  const searchParams = useSearchParams();
  const returnUrl = searchParams.get("returnUrl");
  const reason = searchParams.get("reason");
  const showSessionExpired = reason === "session_expired";
  const showOauthFailed = reason === "oauth_failed";
  const [mode, setMode] = useState<AuthMode>("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function submit(): Promise<void> {
    setSubmitting(true);
    setMessage(null);
    setError(null);

    try {
      const endpoint = mode === "login" ? "/api/auth/login" : "/api/auth/signup";
      const res = await fetch(endpoint, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      const payload = (await res.json()) as {
        error?: string;
        needs_email_confirmation?: boolean;
      };
      if (!res.ok) {
        setError(payload.error ?? t.auth.authFailed);
        return;
      }
      if (mode === "signup" && payload.needs_email_confirmation) {
        setMessage(t.auth.signupSuccess);
        return;
      }
      const redirectTo = returnUrl && isValidAuthReturnPath(returnUrl) ? returnUrl : "/dashboard";
      window.location.href = redirectTo;
    } catch {
      setError(t.auth.requestError);
    } finally {
      setSubmitting(false);
    }
  }

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

  return (
    <main className="mx-auto w-full max-w-md px-6 py-16">
      <div className="rounded-lg border border-[#2a2e39] bg-[#1e222d] p-6">
        <h1 className="text-lg font-semibold text-[#d1d4dc]">
          {mode === "login" ? t.auth.login : t.auth.signup}
        </h1>
        <p className="mt-2 text-sm text-[#868993]">
          {t.auth.description}
        </p>

        {showSessionExpired ? (
          <p className="mt-3 rounded border border-[#2962ff]/40 bg-[#1a2744]/50 px-3 py-2 text-sm text-[#7eb8ff]">
            {t.auth.sessionExpired}
          </p>
        ) : null}
        {showOauthFailed ? (
          <p className="mt-3 rounded border border-[#ef5350]/30 bg-[#2d1f1f]/50 px-3 py-2 text-sm text-[#ef5350]">
            {t.auth.oauthFailed}
          </p>
        ) : null}

        <div className="mt-4">
          <a
            className="flex w-full items-center justify-center gap-2 rounded border border-[#2a2e39] bg-[#131722] px-3 py-2.5 text-sm font-medium text-[#d1d4dc] transition-colors hover:border-[#4285f4]/60 hover:bg-[#1a1d24]"
            href={`/api/auth/oauth/google${
              returnUrl && isValidAuthReturnPath(returnUrl)
                ? `?returnUrl=${encodeURIComponent(returnUrl)}`
                : ""
            }`}
          >
            <GoogleGlyph />
            {t.auth.continueWithGoogle}
          </a>
          <p className="my-4 text-center text-xs text-[#868993]">{t.auth.orUseEmail}</p>
        </div>

        <div className="space-y-3">
          <input
            autoComplete="email"
            className="w-full rounded border border-[#2a2e39] bg-[#131722] px-3 py-2 text-sm text-[#d1d4dc] outline-none focus:border-[#2962ff]"
            onChange={(event) => setEmail(event.target.value)}
            placeholder="Email"
            type="email"
            value={email}
          />
          <input
            autoComplete={mode === "login" ? "current-password" : "new-password"}
            className="w-full rounded border border-[#2a2e39] bg-[#131722] px-3 py-2 text-sm text-[#d1d4dc] outline-none focus:border-[#2962ff]"
            onChange={(event) => setPassword(event.target.value)}
            placeholder="Password (8+ chars)"
            type="password"
            value={password}
          />
        </div>

        {error ? (
          <p className="mt-3 rounded border border-[#ef5350]/30 bg-[#2d1f1f]/50 px-3 py-2 text-sm text-[#ef5350]">
            {error}
          </p>
        ) : null}
        {message ? (
          <p className="mt-3 rounded border border-[#26a69a]/30 bg-[#183d3a]/50 px-3 py-2 text-sm text-[#7ce6dc]">
            {message}
          </p>
        ) : null}

        <button
          className="mt-4 w-full rounded bg-[#2962ff] px-3 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-60"
          disabled={submitting}
          onClick={submit}
          type="button"
        >
          {submitting ? t.auth.submitting : mode === "login" ? t.auth.login : t.auth.signup}
        </button>

        <button
          className="mt-3 w-full rounded border border-[#2a2e39] px-3 py-2 text-sm text-[#d1d4dc] transition-colors hover:border-[#2962ff]"
          onClick={() => {
            setError(null);
            setMessage(null);
            setMode((prev) => (prev === "login" ? "signup" : "login"));
          }}
          type="button"
        >
          {mode === "login" ? t.auth.switchToSignup : t.auth.switchToLogin}
        </button>
      </div>
    </main>
  );
}

function GoogleGlyph() {
  return (
    <svg aria-hidden className="h-5 w-5 shrink-0" viewBox="0 0 24 24">
      <path
        d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"
        fill="#4285F4"
      />
      <path
        d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"
        fill="#34A853"
      />
      <path
        d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"
        fill="#FBBC05"
      />
      <path
        d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"
        fill="#EA4335"
      />
    </svg>
  );
}
