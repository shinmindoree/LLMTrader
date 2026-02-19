"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useI18n } from "@/lib/i18n";

type SessionUser = {
  id: string;
  email: string | null;
};

function isAuthEnabled(): boolean {
  const raw = (process.env.NEXT_PUBLIC_SUPABASE_AUTH_ENABLED ?? "").trim().toLowerCase();
  return raw === "1" || raw === "true" || raw === "yes" || raw === "on";
}

export function AuthActions() {
  const { t } = useI18n();
  const authEnabled = isAuthEnabled();
  const [user, setUser] = useState<SessionUser | null>(null);
  const [loading, setLoading] = useState(authEnabled);

  useEffect(() => {
    if (!authEnabled) {
      return;
    }
    fetch("/api/auth/session", { cache: "no-store" })
      .then(async (res) => {
        if (!res.ok) {
          setUser(null);
          return;
        }
        const payload = (await res.json()) as { user?: SessionUser };
        setUser(payload.user ?? null);
      })
      .catch(() => setUser(null))
      .finally(() => setLoading(false));
  }, [authEnabled]);

  if (!authEnabled) {
    return null;
  }

  if (loading) {
    return <span className="text-xs text-[#868993]">Auth...</span>;
  }

  if (!user) {
    return (
      <Link
        className="rounded border border-[#2a2e39] px-2 py-1 text-xs text-[#d1d4dc] transition-colors hover:border-[#2962ff]"
        href="/auth"
      >
        {t.auth.login}
      </Link>
    );
  }

  async function onLogout(): Promise<void> {
    await fetch("/api/auth/logout", { method: "POST" });
    window.location.href = "/auth";
  }

  return (
    <div className="flex items-center gap-2">
      <span className="max-w-[180px] truncate text-xs text-[#9aa0ad]">{user.email ?? user.id}</span>
      <button
        className="rounded border border-[#2a2e39] px-2 py-1 text-xs text-[#d1d4dc] transition-colors hover:border-[#ef5350]"
        onClick={onLogout}
        type="button"
      >
        {t.auth.logout}
      </button>
    </div>
  );
}
