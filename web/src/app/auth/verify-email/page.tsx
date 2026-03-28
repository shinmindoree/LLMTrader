"use client";

import { useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { useI18n } from "@/lib/i18n";

export default function VerifyEmailPage() {
  const { t } = useI18n();
  const searchParams = useSearchParams();
  const token = searchParams.get("token");
  const email = searchParams.get("email");

  const missingParams = !token || !email;
  const [status, setStatus] = useState<"loading" | "success" | "already" | "error">(missingParams ? "error" : "loading");
  const [errorMsg, setErrorMsg] = useState(missingParams ? "Invalid verification link." : "");

  useEffect(() => {
    if (!token || !email) {
      return;
    }

    fetch(`/api/auth/verify-email?token=${encodeURIComponent(token)}&email=${encodeURIComponent(email)}`)
      .then(async (res) => {
        if (res.ok) {
          const data = await res.json();
          setStatus(data.already_verified ? "already" : "success");
        } else {
          const data = await res.json().catch(() => ({}));
          setStatus("error");
          setErrorMsg(data.error ?? "Verification failed.");
        }
      })
      .catch(() => {
        setStatus("error");
        setErrorMsg("Network error. Please try again.");
      });
  }, [token, email]);

  return (
    <main className="mx-auto w-full max-w-md px-6 py-16">
      <div className="rounded-lg border border-[#2a2e39] bg-[#1e222d] p-6 text-center">
        {status === "loading" && (
          <>
            <div className="mx-auto mb-4 h-12 w-12 animate-spin rounded-full border-4 border-[#2a2e39] border-t-[#2962ff]" />
            <h1 className="text-lg font-semibold text-[#d1d4dc]">
              Verifying your email...
            </h1>
          </>
        )}

        {status === "success" && (
          <>
            <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-full bg-[#26a69a]/20">
              <svg className="h-7 w-7 text-[#26a69a]" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
              </svg>
            </div>
            <h1 className="text-lg font-semibold text-[#d1d4dc]">
              {t.auth.verifyEmailSuccess ?? "Email verified!"}
            </h1>
            <p className="mt-2 text-sm text-[#868993]">
              {t.auth.verifyEmailSuccessDesc ?? "Your account is now active. You can sign in."}
            </p>
            <Link
              href="/auth"
              className="mt-6 inline-block rounded bg-[#2962ff] px-6 py-2.5 text-sm font-medium text-white transition-colors hover:bg-[#2962ff]/90"
            >
              {t.auth.login}
            </Link>
          </>
        )}

        {status === "already" && (
          <>
            <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-full bg-[#2962ff]/20">
              <svg className="h-7 w-7 text-[#2962ff]" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
            </div>
            <h1 className="text-lg font-semibold text-[#d1d4dc]">
              {t.auth.alreadyVerified ?? "Already verified"}
            </h1>
            <p className="mt-2 text-sm text-[#868993]">
              {t.auth.alreadyVerifiedDesc ?? "This email has already been verified. You can sign in."}
            </p>
            <Link
              href="/auth"
              className="mt-6 inline-block rounded bg-[#2962ff] px-6 py-2.5 text-sm font-medium text-white transition-colors hover:bg-[#2962ff]/90"
            >
              {t.auth.login}
            </Link>
          </>
        )}

        {status === "error" && (
          <>
            <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-full bg-[#ef5350]/20">
              <svg className="h-7 w-7 text-[#ef5350]" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </div>
            <h1 className="text-lg font-semibold text-[#d1d4dc]">
              {t.auth.verifyEmailFailed ?? "Verification failed"}
            </h1>
            <p className="mt-2 text-sm text-[#ef5350]">
              {errorMsg}
            </p>
            <Link
              href="/auth"
              className="mt-6 inline-block rounded border border-[#2a2e39] px-6 py-2.5 text-sm font-medium text-[#d1d4dc] transition-colors hover:bg-[#262a35]"
            >
              {t.auth.switchToLogin ?? "Back to Login"}
            </Link>
          </>
        )}
      </div>
    </main>
  );
}
