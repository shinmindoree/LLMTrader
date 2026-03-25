"use client";

import Image from "next/image";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useSession } from "next-auth/react";
import { LanguageSwitcher } from "@/components/LanguageSwitcher";
import { UserProfileMenu } from "@/components/UserProfileMenu";
import { useI18n } from "@/lib/i18n";

const NAV_LINKS = [
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

  return (
    <header className="sticky top-0 z-50 h-14 border-b border-[#2a2e39] bg-[#1e222d]/90 backdrop-blur">
      <div className="flex h-14 w-full items-center justify-between px-4">
        <div className="flex items-center gap-1">
          <Link
            aria-label="Go to homepage"
            className="flex items-center gap-2 rounded px-2 py-1 hover:bg-[#2a2e39] transition-colors"
            href="/"
          >
            <Image alt="AlphaWeaver" height={24} priority src="/alphaweaver-logo.svg" width={24} />
            <span className="text-sm font-semibold text-[#d1d4dc]">AlphaWeaver</span>
          </Link>
          <nav className="ml-4 hidden items-center gap-1 sm:flex">
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
        <div className="flex items-center gap-3">
          {!isLoggedIn && status !== "loading" && (
            <Link
              className="rounded border border-[#2a2e39] px-3 py-1.5 text-sm font-medium text-[#d1d4dc] transition-colors hover:border-[#2962ff] hover:bg-[#2962ff]/10"
              href="/auth"
            >
              {t.auth.login}
            </Link>
          )}
          <LanguageSwitcher />
          <UserProfileMenu />
        </div>
      </div>
    </header>
  );
}
