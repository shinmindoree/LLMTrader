"use client";

import Image from "next/image";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useSession } from "next-auth/react";
import { useEffect, useState } from "react";
import { LanguageSwitcher } from "@/components/LanguageSwitcher";
import { ThemeToggle } from "@/components/ThemeToggle";
import { UserProfileMenu } from "@/components/UserProfileMenu";
import { useI18n } from "@/lib/i18n";

const NAV_LINKS = [
  { href: "/chart", labelKey: "chart" as const },
  { href: "/dashboard", labelKey: "dashboard" as const },
  { href: "/strategies", labelKey: "strategies" as const },
  { href: "/backtest", labelKey: "backtest" as const },
  { href: "/live", labelKey: "live" as const },
];

export function Header() {
  const pathname = usePathname();
  const { t } = useI18n();
  const { status } = useSession();
  const isLoggedIn = status === "authenticated";
  const [mobileNavOpen, setMobileNavOpen] = useState(false);

  useEffect(() => {
    const id = requestAnimationFrame(() => setMobileNavOpen(false));
    return () => cancelAnimationFrame(id);
  }, [pathname]);

  useEffect(() => {
    if (!mobileNavOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setMobileNavOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [mobileNavOpen]);

  return (
    <header className="sticky top-0 z-50 border-b border-[#2a2e39] bg-[#1e222d]/90 backdrop-blur">
      <div className="flex h-14 w-full items-center justify-between px-4">
        <div className="flex min-w-0 flex-1 items-center gap-1">
          <Link
            aria-label="Go to homepage"
            className="flex min-w-0 shrink items-center gap-2 rounded px-2 py-1 hover:bg-[#2a2e39] transition-colors"
            href="/"
          >
            <Image alt="AlphaWeaver" height={24} priority src="/alphaweaver-logo.svg" width={24} />
            <span className="truncate text-sm font-semibold text-[#d1d4dc]">AlphaWeaver</span>
            <span className="hidden rounded bg-[#F0B90B]/15 px-1.5 py-0.5 text-[10px] font-semibold text-[#F0B90B] sm:inline-block">
              for Binance Futures
            </span>
          </Link>
          <nav className="ml-4 hidden items-center gap-1 sm:flex" aria-label="Main">
            {NAV_LINKS.map((link) => {
              const active = pathname === link.href || pathname.startsWith(`${link.href}/`);
              return (
                <Link
                  key={link.href}
                  href={link.href}
                  className={[
                    "relative rounded px-3 py-1.5 text-sm font-medium transition-colors",
                    active
                      ? "text-[#d1d4dc] bg-[#2a2e39]"
                      : "text-[#868993] hover:text-[#d1d4dc] hover:bg-[#2a2e39]/50",
                  ].join(" ")}
                >
                  {t.nav[link.labelKey]}
                </Link>
              );
            })}
          </nav>
        </div>
        <div className="flex shrink-0 items-center gap-2 sm:gap-3">
          <button
            type="button"
            className="inline-flex h-10 w-10 items-center justify-center rounded-md border border-[#2a2e39] text-[#d1d4dc] transition-colors hover:bg-[#2a2e39] sm:hidden"
            aria-expanded={mobileNavOpen}
            aria-controls="mobile-main-nav"
            onClick={() => setMobileNavOpen((o) => !o)}
          >
            <span className="sr-only">{mobileNavOpen ? t.nav.closeMenu : t.nav.openMenu}</span>
            {mobileNavOpen ? (
              <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.75} aria-hidden>
                <path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" />
              </svg>
            ) : (
              <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.75} aria-hidden>
                <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 6.75h16.5M3.75 12h16.5m-16.5 5.25h16.5" />
              </svg>
            )}
          </button>
          {!isLoggedIn && status !== "loading" && (
            <Link
              className="rounded border border-[#2a2e39] px-3 py-1.5 text-sm font-medium text-[#d1d4dc] transition-colors hover:border-[#2962ff] hover:bg-[#2962ff]/10"
              href="/auth"
            >
              {t.auth.login}
            </Link>
          )}
          <LanguageSwitcher />
          <ThemeToggle />
          <UserProfileMenu />
        </div>
      </div>
      {mobileNavOpen ? (
        <nav
          id="mobile-main-nav"
          className="border-t border-[#2a2e39] bg-[#1e222d] px-4 py-3 sm:hidden"
          aria-label="Main"
        >
          <ul className="flex flex-col gap-1">
            {NAV_LINKS.map((link) => {
              const active = pathname === link.href || pathname.startsWith(`${link.href}/`);
              return (
                <li key={link.href}>
                  <Link
                    href={link.href}
                    className={[
                      "block rounded-lg px-3 py-2.5 text-sm font-medium transition-colors",
                      active
                        ? "bg-[#2a2e39] text-[#d1d4dc]"
                        : "text-[#868993] hover:bg-[#2a2e39]/70 hover:text-[#d1d4dc]",
                    ].join(" ")}
                    onClick={() => setMobileNavOpen(false)}
                  >
                    {t.nav[link.labelKey]}
                  </Link>
                </li>
              );
            })}
          </ul>
        </nav>
      ) : null}
    </header>
  );
}
