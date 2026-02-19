"use client";

import { useState } from "react";

type AuthMode = "login" | "signup";

function isAuthEnabled(): boolean {
  const raw = (process.env.NEXT_PUBLIC_SUPABASE_AUTH_ENABLED ?? "").trim().toLowerCase();
  return raw === "1" || raw === "true" || raw === "yes" || raw === "on";
}

export default function AuthPage() {
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
        setError(payload.error ?? "Authentication failed");
        return;
      }
      if (mode === "signup" && payload.needs_email_confirmation) {
        setMessage("회원가입 완료. 이메일 인증 후 로그인하세요.");
        return;
      }
      window.location.href = "/dashboard";
    } catch {
      setError("요청 처리 중 오류가 발생했습니다.");
    } finally {
      setSubmitting(false);
    }
  }

  if (!isAuthEnabled()) {
    return (
      <main className="mx-auto w-full max-w-md px-6 py-16">
        <div className="rounded-lg border border-[#2a2e39] bg-[#1e222d] p-6">
          <h1 className="text-lg font-semibold text-[#d1d4dc]">Auth Disabled</h1>
          <p className="mt-2 text-sm text-[#868993]">
            Supabase auth is disabled. Set <code>NEXT_PUBLIC_SUPABASE_AUTH_ENABLED=true</code>.
          </p>
        </div>
      </main>
    );
  }

  return (
    <main className="mx-auto w-full max-w-md px-6 py-16">
      <div className="rounded-lg border border-[#2a2e39] bg-[#1e222d] p-6">
        <h1 className="text-lg font-semibold text-[#d1d4dc]">
          {mode === "login" ? "로그인" : "회원가입"}
        </h1>
        <p className="mt-2 text-sm text-[#868993]">
          Supabase 이메일/비밀번호 인증을 사용합니다.
        </p>

        <div className="mt-4 space-y-3">
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
          {submitting ? "처리 중..." : mode === "login" ? "로그인" : "회원가입"}
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
          {mode === "login" ? "회원가입으로 전환" : "로그인으로 전환"}
        </button>
      </div>
    </main>
  );
}
