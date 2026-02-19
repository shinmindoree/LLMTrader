"use client";

import { useI18n } from "@/lib/i18n";

export function LanguageSwitcher() {
  const { locale, setLocale } = useI18n();

  return (
    <select
      aria-label="Language"
      className="rounded border border-[#2a2e39] bg-[#131722] px-2 py-1 text-xs text-[#d1d4dc] outline-none focus:border-[#2962ff]"
      value={locale}
      onChange={(e) => setLocale(e.target.value as "en" | "ko")}
    >
      <option value="en">EN</option>
      <option value="ko">KO</option>
    </select>
  );
}
