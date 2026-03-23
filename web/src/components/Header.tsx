"use client";

import Image from "next/image";
import Link from "next/link";
import { LanguageSwitcher } from "@/components/LanguageSwitcher";
import { UserProfileMenu } from "@/components/UserProfileMenu";

export function Header() {
  return (
    <header className="sticky top-0 z-50 h-14 border-b border-[#2a2e39] bg-[#1e222d]/90 backdrop-blur">
      <div className="flex h-14 w-full items-center justify-between px-4">
        <Link
          aria-label="Go to homepage"
          className="flex items-center gap-2 rounded px-2 py-1 hover:bg-[#2a2e39] transition-colors"
          href="/"
        >
          <Image alt="AlphaWeaver" height={24} priority src="/alphaweaver-logo.svg" width={24} />
          <span className="text-sm font-semibold text-[#d1d4dc]">AlphaWeaver</span>
        </Link>
        <div className="flex items-center gap-3">
          <LanguageSwitcher />
          <UserProfileMenu />
        </div>
      </div>
    </header>
  );
}
