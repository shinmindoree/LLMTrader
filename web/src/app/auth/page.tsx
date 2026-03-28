"use client";

import { useState, useRef, type FormEvent } from "react";
import { useSearchParams } from "next/navigation";
import { signIn } from "next-auth/react";
import ReCAPTCHA from "react-google-recaptcha";
import { isValidAuthReturnPath } from "@/lib/authRedirect";
import { useI18n } from "@/lib/i18n";

const RECAPTCHA_SITE_KEY = process.env.NEXT_PUBLIC_RECAPTCHA_SITE_KEY ?? "";

function getPasswordStrength(pw: string) {
  const checks = {
    minLength: pw.length >= 8,
    hasLetter: /[a-zA-Z]/.test(pw),
    hasNumber: /\d/.test(pw),
    hasSpecial: /[^a-zA-Z0-9]/.test(pw),
  };
  const score = Object.values(checks).filter(Boolean).length;
  return { ...checks, score };
}

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
  const [showPassword, setShowPassword] = useState(false);
  const [showPasswordConfirm, setShowPasswordConfirm] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [credError, setCredError] = useState("");
  const [signupSuccess, setSignupSuccess] = useState(false);
  const [emailNotVerified, setEmailNotVerified] = useState(false);
  const [resending, setResending] = useState(false);
  const [resendDone, setResendDone] = useState(false);
  const recaptchaRef = useRef<ReCAPTCHA>(null);
  const [captchaToken, setCaptchaToken] = useState<string | null>(null);

  const pwStrength = getPasswordStrength(password);
  const strengthColors = ["#ef5350", "#ff9800", "#ffeb3b", "#26a69a"];
  const strengthLabels = [
    t.auth.pwWeak ?? "Weak",
    t.auth.pwFair ?? "Fair",
    t.auth.pwGood ?? "Good",
    t.auth.pwStrong ?? "Strong",
  ];

  async function handleCredentialsSubmit(e: FormEvent) {
    e.preventDefault();
    setCredError("");
    setEmailNotVerified(false);
    setSubmitting(true);

    if (mode === "signup") {
      if (!pwStrength.minLength || !pwStrength.hasLetter || !pwStrength.hasNumber) {
        setCredError(t.auth.pwRequirementsNotMet ?? "Password does not meet requirements.");
        setSubmitting(false);
        return;
      }
      if (password !== passwordConfirm) {
        setCredError(t.auth.passwordsDoNotMatch ?? "Passwords do not match");
        setSubmitting(false);
        return;
      }
      if (RECAPTCHA_SITE_KEY && !captchaToken) {
        setCredError(t.auth.captchaRequired ?? "Please verify that you are not a robot.");
        setSubmitting(false);
        return;
      }

      try {
        const res = await fetch("/api/auth/signup", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ email, password, captchaToken }),
        });
        const data = await res.json();
        if (!res.ok) {
          setCredError(data.error || t.auth.requestError);
          recaptchaRef.current?.reset();
          setCaptchaToken(null);
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
          <div className="mx-auto mb-6 flex h-16 w-16 items-center justify-center rounded-full bg-[#2962ff]/10">
            <svg className="h-8 w-8 text-[#2962ff]" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" d="M21.75 6.75v10.5a2.25 2.25 0 0 1-2.25 2.25h-15a2.25 2.25 0 0 1-2.25-2.25V6.75m19.5 0A2.25 2.25 0 0 0 19.5 4.5h-15a2.25 2.25 0 0 0-2.25 2.25m19.5 0v.243a2.25 2.25 0 0 1-1.07 1.916l-7.5 4.615a2.25 2.25 0 0 1-2.36 0L3.32 8.91a2.25 2.25 0 0 1-1.07-1.916V6.75" />
            </svg>
          </div>

          <h2 className="text-xl font-semibold text-[#d1d4dc]">{t.auth.checkYourEmail}</h2>
          <p className="mt-3 text-sm leading-relaxed text-[#868993]">{t.auth.verificationSentTo}</p>
          <p className="mt-1 text-sm font-medium text-[#d1d4dc]">{email}</p>
          <p className="mt-3 text-sm leading-relaxed text-[#868993]">{t.auth.clickLinkToActivate}</p>

          <div className="mt-6 rounded-lg border border-[#2a2e39] bg-[#131722] px-4 py-3">
            <p className="text-xs text-[#868993]">{t.auth.didntReceiveEmail}</p>
            {resendDone ? (
              <p className="mt-2 text-sm font-medium text-[#26a69a]">{t.auth.resendDone}</p>
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

          <button
            type="button"
            className="mt-6 text-sm text-[#868993] hover:text-[#d1d4dc]"
            onClick={() => { setEmailNotVerified(false); setCredError(""); setResendDone(false); }}
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
        {/* Header */}
        <h1 className="text-center text-xl font-bold text-[#d1d4dc]">
          {mode === "login" ? t.auth.login : t.auth.signupWithEmail}
        </h1>
        <p className="mt-2 text-center text-sm text-[#868993]">
          {mode === "login" ? t.auth.description : t.auth.signupDescription}
        </p>

        {/* Banners */}
        {showSessionExpired ? (
          <p className="mt-4 rounded border border-[#2962ff]/40 bg-[#1a2744]/50 px-3 py-2 text-sm text-[#7eb8ff]">
            {t.auth.sessionExpired}
          </p>
        ) : null}
        {showAuthFailed ? (
          <p className="mt-4 rounded border border-[#ef5350]/30 bg-[#2d1f1f]/50 px-3 py-2 text-sm text-[#ef5350]">
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
          className="mt-6 flex w-full items-center justify-center gap-3 rounded-lg border border-[#2a2e39] bg-[#131722] px-4 py-3 text-sm font-medium text-[#d1d4dc] transition-colors hover:bg-[#1a1f2e]"
          onClick={() => signIn("google", { callbackUrl })}
        >
          <GoogleGlyph />
          {t.auth.continueWithGoogle}
        </button>

        {/* Divider */}
        <div className="my-5 flex items-center gap-3">
          <div className="h-px flex-1 bg-[#2a2e39]" />
          <span className="text-xs text-[#868993]">{t.auth.orUseEmail}</span>
          <div className="h-px flex-1 bg-[#2a2e39]" />
        </div>

        {/* Credentials Form */}
        <form onSubmit={handleCredentialsSubmit} className="space-y-4">
          {credError ? (
            <p className="rounded border border-[#ef5350]/30 bg-[#2d1f1f]/50 px-3 py-2 text-sm text-[#ef5350]">
              {credError}
            </p>
          ) : null}

          {/* Email field */}
          <div>
            <label htmlFor="email" className="mb-1.5 block text-xs font-medium text-[#868993]">
              {t.auth.emailLabel ?? "Email"}
            </label>
            <div className="relative">
              <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-[#868993]">
                <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M21.75 6.75v10.5a2.25 2.25 0 0 1-2.25 2.25h-15a2.25 2.25 0 0 1-2.25-2.25V6.75m19.5 0A2.25 2.25 0 0 0 19.5 4.5h-15a2.25 2.25 0 0 0-2.25 2.25m19.5 0v.243a2.25 2.25 0 0 1-1.07 1.916l-7.5 4.615a2.25 2.25 0 0 1-2.36 0L3.32 8.91a2.25 2.25 0 0 1-1.07-1.916V6.75" />
                </svg>
              </span>
              <input
                id="email"
                name="email"
                type="email"
                required
                placeholder="name@example.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="w-full rounded-lg border border-[#2a2e39] bg-[#131722] py-2.5 pl-10 pr-3 text-sm text-[#d1d4dc] placeholder-[#5d6068] outline-none focus:border-[#2962ff]"
              />
            </div>
          </div>

          {/* Password field */}
          <div>
            <label htmlFor="password" className="mb-1.5 block text-xs font-medium text-[#868993]">
              {t.auth.passwordLabel ?? "Password"}
            </label>
            <div className="relative">
              <input
                id="password"
                name="password"
                type={showPassword ? "text" : "password"}
                required
                placeholder={mode === "signup" ? (t.auth.pwPlaceholder ?? "Create a password") : "••••••••"}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="w-full rounded-lg border border-[#2a2e39] bg-[#131722] py-2.5 pl-3 pr-10 text-sm text-[#d1d4dc] placeholder-[#5d6068] outline-none focus:border-[#2962ff]"
              />
              <button
                type="button"
                tabIndex={-1}
                onClick={() => setShowPassword(!showPassword)}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-[#868993] hover:text-[#d1d4dc]"
              >
                {showPassword ? (
                  <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M3.98 8.223A10.477 10.477 0 0 0 1.934 12C3.226 16.338 7.244 19.5 12 19.5c.993 0 1.953-.138 2.863-.395M6.228 6.228A10.451 10.451 0 0 1 12 4.5c4.756 0 8.773 3.162 10.065 7.498a10.522 10.522 0 0 1-4.293 5.774M6.228 6.228 3 3m3.228 3.228 3.65 3.65m7.894 7.894L21 21m-3.228-3.228-3.65-3.65m0 0a3 3 0 1 0-4.243-4.243m4.242 4.242L9.88 9.88" />
                  </svg>
                ) : (
                  <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M2.036 12.322a1.012 1.012 0 0 1 0-.639C3.423 7.51 7.36 4.5 12 4.5c4.638 0 8.573 3.007 9.963 7.178.07.207.07.431 0 .639C20.577 16.49 16.64 19.5 12 19.5c-4.638 0-8.573-3.007-9.963-7.178Z" />
                    <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z" />
                  </svg>
                )}
              </button>
            </div>

            {/* Password requirements (signup only) */}
            {mode === "signup" && (
              <div className="mt-2 space-y-1.5">
                {/* Strength meter */}
                {password.length > 0 && (
                  <div className="flex items-center gap-2">
                    <div className="flex flex-1 gap-1">
                      {[0, 1, 2, 3].map((i) => (
                        <div
                          key={i}
                          className="h-1 flex-1 rounded-full transition-colors"
                          style={{
                            backgroundColor: i < pwStrength.score ? strengthColors[pwStrength.score - 1] : "#2a2e39",
                          }}
                        />
                      ))}
                    </div>
                    <span className="text-[10px] font-medium" style={{ color: strengthColors[pwStrength.score - 1] ?? "#5d6068" }}>
                      {pwStrength.score > 0 ? strengthLabels[pwStrength.score - 1] : ""}
                    </span>
                  </div>
                )}
                {/* Requirement checklist */}
                <ul className="space-y-0.5">
                  <PasswordRule met={pwStrength.minLength} label={t.auth.pwRuleLength ?? "At least 8 characters"} />
                  <PasswordRule met={pwStrength.hasLetter} label={t.auth.pwRuleLetter ?? "Contains a letter"} />
                  <PasswordRule met={pwStrength.hasNumber} label={t.auth.pwRuleNumber ?? "Contains a number"} />
                </ul>
              </div>
            )}
          </div>

          {/* Confirm password (signup) */}
          {mode === "signup" ? (
            <div>
              <label htmlFor="passwordConfirm" className="mb-1.5 block text-xs font-medium text-[#868993]">
                {t.auth.confirmPasswordLabel ?? "Confirm password"}
              </label>
              <div className="relative">
                <input
                  id="passwordConfirm"
                  name="passwordConfirm"
                  type={showPasswordConfirm ? "text" : "password"}
                  required
                  placeholder="••••••••"
                  value={passwordConfirm}
                  onChange={(e) => setPasswordConfirm(e.target.value)}
                  className="w-full rounded-lg border border-[#2a2e39] bg-[#131722] py-2.5 pl-3 pr-10 text-sm text-[#d1d4dc] placeholder-[#5d6068] outline-none focus:border-[#2962ff]"
                />
                <button
                  type="button"
                  tabIndex={-1}
                  onClick={() => setShowPasswordConfirm(!showPasswordConfirm)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-[#868993] hover:text-[#d1d4dc]"
                >
                  {showPasswordConfirm ? (
                    <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" d="M3.98 8.223A10.477 10.477 0 0 0 1.934 12C3.226 16.338 7.244 19.5 12 19.5c.993 0 1.953-.138 2.863-.395M6.228 6.228A10.451 10.451 0 0 1 12 4.5c4.756 0 8.773 3.162 10.065 7.498a10.522 10.522 0 0 1-4.293 5.774M6.228 6.228 3 3m3.228 3.228 3.65 3.65m7.894 7.894L21 21m-3.228-3.228-3.65-3.65m0 0a3 3 0 1 0-4.243-4.243m4.242 4.242L9.88 9.88" />
                    </svg>
                  ) : (
                    <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" d="M2.036 12.322a1.012 1.012 0 0 1 0-.639C3.423 7.51 7.36 4.5 12 4.5c4.638 0 8.573 3.007 9.963 7.178.07.207.07.431 0 .639C20.577 16.49 16.64 19.5 12 19.5c-4.638 0-8.573-3.007-9.963-7.178Z" />
                      <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z" />
                    </svg>
                  )}
                </button>
              </div>
              {passwordConfirm && password !== passwordConfirm && (
                <p className="mt-1 text-xs text-[#ef5350]">{t.auth.passwordsDoNotMatch ?? "Passwords do not match"}</p>
              )}
            </div>
          ) : null}

          {/* reCAPTCHA (signup only) */}
          {mode === "signup" && RECAPTCHA_SITE_KEY ? (
            <div className="flex justify-center">
              <ReCAPTCHA
                ref={recaptchaRef}
                sitekey={RECAPTCHA_SITE_KEY}
                theme="dark"
                onChange={(token) => setCaptchaToken(token)}
                onExpired={() => setCaptchaToken(null)}
              />
            </div>
          ) : null}

          {/* Submit */}
          <button
            type="submit"
            disabled={submitting}
            className="w-full rounded-lg bg-[#2962ff] px-3 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-[#234dd4] disabled:opacity-60"
          >
            {submitting
              ? t.auth.submitting
              : mode === "login"
                ? t.auth.login
                : (t.auth.createAccount ?? "Create account")}
          </button>
        </form>

        {/* Toggle login/signup */}
        <div className="mt-5 text-center">
          <span className="text-sm text-[#868993]">
            {mode === "login" ? (t.auth.noAccount ?? "Don\u2019t have an account?") : (t.auth.haveAccount ?? "Already have an account?")}
          </span>{" "}
          <button
            type="button"
            className="text-sm font-medium text-[#2962ff] hover:underline"
            onClick={() => {
              setMode(mode === "login" ? "signup" : "login");
              setCredError("");
              setSignupSuccess(false);
              setCaptchaToken(null);
              recaptchaRef.current?.reset();
            }}
          >
            {mode === "login" ? t.auth.signup : t.auth.login}
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

function PasswordRule({ met, label }: { met: boolean; label: string }) {
  return (
    <li className="flex items-center gap-1.5 text-[11px]">
      {met ? (
        <svg className="h-3 w-3 text-[#26a69a]" fill="none" viewBox="0 0 24 24" strokeWidth={2.5} stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" />
        </svg>
      ) : (
        <svg className="h-3 w-3 text-[#5d6068]" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
          <circle cx="12" cy="12" r="6" />
        </svg>
      )}
      <span className={met ? "text-[#868993]" : "text-[#5d6068]"}>{label}</span>
    </li>
  );
}
