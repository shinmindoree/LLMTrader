"use client";

import { LoadingSpinner } from "@/components/LoadingSpinner";

type InlineLoadingIndicatorProps = {
  message: string;
  className?: string;
};

export function InlineLoadingIndicator({ message, className = "" }: InlineLoadingIndicatorProps) {
  return (
    <div
      className={`flex items-center justify-center gap-3 rounded-lg border border-[#2a2e39] bg-[#1e222d] px-4 py-6 ${className}`}
      role="status"
      aria-live="polite"
      aria-label={message}
    >
      <LoadingSpinner size="sm" />
      <span className="text-sm text-[#868993]">{message}</span>
    </div>
  );
}
