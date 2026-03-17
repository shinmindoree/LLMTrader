"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { useI18n } from "@/lib/i18n";

type SessionData = {
  user?: { id: string; email: string | null };
  isAdmin?: boolean;
};

function isAuthEnabled(): boolean {
  const raw = (
    process.env.NEXT_PUBLIC_SUPABASE_AUTH_ENABLED ?? ""
  )
    .trim()
    .toLowerCase();
  return raw === "1" || raw === "true" || raw === "yes" || raw === "on";
}

export function UserProfileMenu() {
  const { t } = useI18n();
  const authEnabled = isAuthEnabled();
  const [session, setSession] = useState<SessionData | null>(null);
  const [loading, setLoading] = useState(authEnabled);
  const [open, setOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!authEnabled) return;
    fetch("/api/auth/session", { cache: "no-store" })
      .then(async (res) => {
        if (!res.ok) {
          setSession(null);
          return;
        }
        setSession((await res.json()) as SessionData);
      })
      .catch(() => setSession(null))
      .finally(() => setLoading(false));
  }, [authEnabled]);

  useEffect(() => {
    if (!open) return;
    function handleClick(e: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [open]);

  if (!authEnabled) return null;

  if (loading) {
    return <span className="text-xs text-[#868993]">Auth...</span>;
  }

  const user = session?.user;
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

  const initial = (user.email ?? user.id).charAt(0).toUpperCase();

  async function onLogout(): Promise<void> {
    await fetch("/api/auth/logout", { method: "POST" });
    window.location.href = "/auth";
  }

  const menuItems = [
    { href: "/settings", label: t.nav.settings },
    { href: "/billing", label: "Billing" },
    ...(session?.isAdmin ? [{ href: "/admin", label: "Admin" }] : []),
  ];

  return (
    <div ref={menuRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex h-8 w-8 items-center justify-center rounded-full bg-[#2a2e39] text-sm font-semibold text-[#d1d4dc] transition-colors hover:bg-[#363a45]"
      >
        {initial}
      </button>

      {open && (
        <div className="absolute right-0 top-full mt-2 w-52 rounded-lg border border-[#2a2e39] bg-[#1e222d] py-1 shadow-xl z-50">
          <div className="border-b border-[#2a2e39] px-4 py-3">
            <p className="truncate text-sm text-[#d1d4dc]">
              {user.email ?? user.id}
            </p>
          </div>

          {menuItems.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              onClick={() => setOpen(false)}
              className="block px-4 py-2.5 text-sm text-[#868993] transition-colors hover:bg-[#252936] hover:text-[#d1d4dc]"
            >
              {item.label}
            </Link>
          ))}

          <div className="border-t border-[#2a2e39]" />
          <button
            type="button"
            onClick={onLogout}
            className="w-full px-4 py-2.5 text-left text-sm text-[#868993] transition-colors hover:bg-[#252936] hover:text-[#ef5350]"
          >
            {t.auth.logout}
          </button>
        </div>
      )}
    </div>
  );
}
