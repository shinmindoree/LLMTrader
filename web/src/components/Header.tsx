"use client";

import Image from "next/image";
import Link from "next/link";
import { AuthActions } from "@/components/AuthActions";
import { LanguageSwitcher } from "@/components/LanguageSwitcher";
import { useI18n } from "@/lib/i18n";

export function Header() {
  const { t } = useI18n();

  return (
    <header className="sticky top-0 z-50 h-14 border-b border-[#2a2e39] bg-[#1e222d]/90 backdrop-blur">
      <div className="flex h-14 w-full items-center justify-between px-4">
        <Link
          aria-label="Go to homepage"
          className="flex items-center gap-2 rounded px-2 py-1 hover:bg-[#2a2e39] transition-colors"
          href="/"
        >
          <Image alt="YHLAB" height={24} priority src="/yhlab-logo.svg" width={24} />
          <span className="text-sm font-semibold text-[#d1d4dc]">YHLAB</span>
        </Link>
        <nav className="flex items-center gap-2 text-sm">
          <Link className="rounded px-2 py-1 text-[#d1d4dc] hover:bg-[#2a2e39] transition-colors" href="/#features">
            {t.nav.features}
          </Link>
          <Link className="rounded px-2 py-1 text-[#d1d4dc] hover:bg-[#2a2e39] transition-colors" href="/dashboard">
            {t.nav.dashboard}
          </Link>
          <Link className="rounded px-2 py-1 text-[#d1d4dc] hover:bg-[#2a2e39] transition-colors" href="/strategies">
            {t.nav.strategies}
          </Link>
          <Link className="rounded px-2 py-1 text-[#d1d4dc] hover:bg-[#2a2e39] transition-colors" href="/backtest">
            {t.nav.backtest}
          </Link>
          <Link className="rounded px-2 py-1 text-[#d1d4dc] hover:bg-[#2a2e39] transition-colors" href="/live">
            {t.nav.live}
          </Link>
          <Link className="rounded px-2 py-1 text-[#d1d4dc] hover:bg-[#2a2e39] transition-colors md:hidden" href="/settings">
            {t.nav.settings}
          </Link>
        </nav>
        <div className="flex items-center gap-3">
          <LanguageSwitcher />
          <AuthActions />
        </div>
      </div>
    </header>
  );
}
