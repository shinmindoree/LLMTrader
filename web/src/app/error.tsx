"use client";

import * as Sentry from "@sentry/nextjs";
import { useEffect } from "react";
import { useI18n } from "@/lib/i18n";

export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  const { t } = useI18n();

  useEffect(() => {
    Sentry.captureException(error);
  }, [error]);

  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center px-6 text-center">
      <div className="rounded-lg border border-[#ef5350]/30 bg-[#2d1f1f]/50 p-8 max-w-md">
        <h2 className="text-xl font-semibold text-[#ef5350] mb-3">
          {t.errorPage.title}
        </h2>
        <p className="text-sm text-[#868993] mb-6">
          {t.errorPage.description}
        </p>
        {error.digest && (
          <p className="text-xs text-[#868993]/60 mb-4 font-mono">
            Error ID: {error.digest}
          </p>
        )}
        <div className="flex gap-3 justify-center">
          <button
            onClick={reset}
            className="rounded bg-[#2962ff] px-5 py-2 text-sm font-medium text-white hover:bg-[#1e53e5] transition-colors"
          >
            {t.errorPage.retry}
          </button>
          <a
            href="/dashboard"
            className="rounded border border-[#2a2e39] px-5 py-2 text-sm font-medium text-[#d1d4dc] hover:bg-[#252936] transition-colors"
          >
            {t.errorPage.goHome}
          </a>
        </div>
      </div>
    </div>
  );
}
