"use client";

import { useI18n } from "@/lib/i18n";
import { LoadingSpinner } from "@/components/LoadingSpinner";

export function PageLoadingSpinner() {
  const { t } = useI18n();

  return (
    <div
      className="flex min-h-[40vh] w-full flex-col items-center justify-center gap-5 px-6 py-12"
      role="status"
      aria-live="polite"
      aria-label={t.common.loading}
    >
      <div className="relative">
        <div
          className="absolute inset-[-10px] rounded-full bg-[#2962ff]/10 blur-md"
          aria-hidden
        />
        <LoadingSpinner size="lg" className="relative" />
      </div>
      <p className="text-sm font-medium tracking-wide text-[#868993]">{t.common.loading}</p>
    </div>
  );
}
