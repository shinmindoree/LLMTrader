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

  const [mode, setMode] = useState<"login" | "signup">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [passwordConfirm, setPasswordConfirm] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [credError, setCredError] = useState("");
  const [signupSuccess, setSignupSuccess] = useState(false);
  const [emailNotVerified, setEmailNotVerified] = useState(false);
  const [resending, setResending] = useState(false);
  const [resendDone, setResendDone] = useState(false);

  async function handleCredentialsSubmit(e: FormEvent) {
    e.preventDefault();
    setCredError("");
    setEmailNotVerified(false);
    setSubmitting(true);

    if (mode === "signup") {
      if (password.length < 8) {
        setCredError("Password must be at least 8 characters");
        setSubmitting(false);
        return;
      }
      if (password !== passwordConfirm) {
        setCredError("Passwords do not match");
        setSubmitting(false);
        return;
      }

      try {
        const res = await fetch("/api/auth/signup", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ email, password }),
        });
        const data = await res.json();
        if (!res.ok) {
          setCredError(data.error || t.auth.requestError);
        } else {
          setSignupSuccess(true);
          setMode("login");
        }
      } catch {
        setCredError(t.auth.requestError);
      } finally {
        setSubmitting(false);
      }
      return;
    }

    // Pre-check credentials to detect unverified email before NextAuth signIn
    try {
      const check = await fetch("/api/auth/check-credentials", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      const checkData = await check.json();

      if (!check.ok) {
        if (checkData.reason === "EMAIL_NOT_VERIFIED") {
          setEmailNotVerified(true);
          setResendDone(false);
          setSubmitting(false);
          return;
        }
        setCredError(t.auth.invalidCredentials);
        setSubmitting(false);
        return;
      }
    } catch {
      // If pre-check fails (network), fall through to NextAuth
    }

    try {
      const result = await signIn("credentials", {
        email,
        password,
        callbackUrl,
        redirect: false,
      });
      if (result?.error) {
        setCredError(t.auth.invalidCredentials);
      } else if (result?.url) {
        window.location.href = result.url;
      }
    } finally {
      setSubmitting(false);
    }
  }

  async function handleResendVerification() {
    setResending(true);
    setResendDone(false);
    try {
      await fetch("/api/auth/resend-verification", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email }),
      });
      setResendDone(true);
    } catch {
      // ignore
    } finally {
      setResending(false);
    }
  }

  // Full-screen email verification prompt
  if (emailNotVerified) {
    return (
      <main className="mx-auto w-full max-w-md px-6 py-16">
        <div className="rounded-lg border border-[#2a2e39] bg-[#1e222d] p-8 text-center">
          {/* Mail icon */}
          <div className="mx-auto mb-6 flex h-16 w-16 items-center justify-center rounded-full bg-[#2962ff]/10">
            <svg className="h-8 w-8 text-[#2962ff]" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" d="M21.75 6.75v10.5a2.25 2.25 0 0 1-2.25 2.25h-15a2.25 2.25 0 0 1-2.25-2.25V6.75m19.5 0A2.25 2.25 0 0 0 19.5 4.5h-15a2.25 2.25 0 0 0-2.25 2.25m19.5 0v.243a2.25 2.25 0 0 1-1.07 1.916l-7.5 4.615a2.25 2.25 0 0 1-2.36 0L3.32 8.91a2.25 2.25 0 0 1-1.07-1.916V6.75" />
            </svg>
          </div>

          <h2 className="text-xl font-semibold text-[#d1d4dc]">
            {t.auth.checkYourEmail}
          </h2>

          <p className="mt-3 text-sm leading-relaxed text-[#868993]">
            {t.auth.verificationSentTo}
          </p>
          <p className="mt-1 text-sm font-medium text-[#d1d4dc]">
            {email}
          </p>
          <p className="mt-3 text-sm leading-relaxed text-[#868993]">
            {t.auth.clickLinkToActivate}
          </p>

          {/* Resend section */}
          <div className="mt-6 rounded-lg border border-[#2a2e39] bg-[#131722] px-4 py-3">
            <p className="text-xs text-[#868993]">
              {t.auth.didntReceiveEmail}
            </p>
            {resendDone ? (
              <p className="mt-2 text-sm font-medium text-[#26a69a]">
                {t.auth.resendDone}
              </p>
            ) : (
              <button
                type="button"
                disabled={resending}
                onClick={handleResendVerification}
                className="mt-2 text-sm font-medium text-[#2962ff] hover:text-[#2962ff]/80 disabled:opacity-60"
              >
                {resending ? t.auth.submitting : t.auth.resendVerification}
              </button>
            )}
          </div>

          {/* Back to login */}
          <button
            type="button"
            className="mt-6 text-sm text-[#868993] hover:text-[#d1d4dc]"
            onClick={() => {
              setEmailNotVerified(false);
              setCredError("");
              setResendDone(false);
            }}
          >
            &larr; {t.auth.backToLogin}
          </button>
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
        {showAuthFailed ? (
          <p className="mt-3 rounded border border-[#ef5350]/30 bg-[#2d1f1f]/50 px-3 py-2 text-sm text-[#ef5350]">
            {t.auth.authFailed}
          </p>
        ) : null}
        {signupSuccess ? (
          <div className="mt-4 rounded-lg border border-[#26a69a]/30 bg-[#1a2e2a]/50 px-4 py-4 text-center">
            <div className="mx-auto mb-3 flex h-10 w-10 items-center justify-center rounded-full bg-[#26a69a]/10">
              <svg className="h-5 w-5 text-[#26a69a]" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" d="M21.75 6.75v10.5a2.25 2.25 0 0 1-2.25 2.25h-15a2.25 2.25 0 0 1-2.25-2.25V6.75m19.5 0A2.25 2.25 0 0 0 19.5 4.5h-15a2.25 2.25 0 0 0-2.25 2.25m19.5 0v.243a2.25 2.25 0 0 1-1.07 1.916l-7.5 4.615a2.25 2.25 0 0 1-2.36 0L3.32 8.91a2.25 2.25 0 0 1-1.07-1.916V6.75" />
              </svg>
            </div>
            <p className="text-sm font-medium text-[#26a69a]">{t.auth.signupSuccess}</p>
            <p className="mt-1 text-xs text-[#868993]">{t.auth.signupSuccessHint}</p>
          </div>
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
            id="email"
            name="email"
            type="email"
            required
            placeholder="Email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="w-full rounded border border-[#2a2e39] bg-[#131722] px-3 py-2 text-sm text-[#d1d4dc] placeholder-[#868993] outline-none focus:border-[#2962ff]"
          />
          <input
            id="password"
            name="password"
            type="password"
            required
            placeholder="Password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="w-full rounded border border-[#2a2e39] bg-[#131722] px-3 py-2 text-sm text-[#d1d4dc] placeholder-[#868993] outline-none focus:border-[#2962ff]"
          />
          {mode === "signup" ? (
            <input
              id="passwordConfirm"
              name="passwordConfirm"
              type="password"
              required
              placeholder="Confirm Password"
              value={passwordConfirm}
              onChange={(e) => setPasswordConfirm(e.target.value)}
              className="w-full rounded border border-[#2a2e39] bg-[#131722] px-3 py-2 text-sm text-[#d1d4dc] placeholder-[#868993] outline-none focus:border-[#2962ff]"
            />
          ) : null}
          <button
            type="submit"
            disabled={submitting}
            className="w-full rounded bg-[#2962ff] px-3 py-2.5 text-sm font-medium text-white transition-colors hover:bg-[#2962ff]/90 disabled:opacity-60"
          >
            {submitting ? t.auth.submitting : mode === "login" ? t.auth.login : t.auth.signup}
          </button>
        </form>

        {/* Toggle login/signup */}
        <div className="mt-4 text-center">
          <button
            type="button"
            className="text-sm text-[#2962ff] hover:underline"
            onClick={() => {
              setMode(mode === "login" ? "signup" : "login");
              setCredError("");
              setSignupSuccess(false);
            }}
          >
            {mode === "login" ? t.auth.switchToSignup : t.auth.switchToLogin}
          </button>
        </div>
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
