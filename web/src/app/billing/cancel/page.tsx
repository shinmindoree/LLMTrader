"use client";

import Link from "next/link";

export default function BillingCancelPage() {
  return (
    <main className="w-full max-w-lg px-6 py-20 mx-auto text-center">
      <div className="rounded-lg border border-[#2a2e39] bg-[#1e222d] p-10">
        <div className="text-5xl mb-4">↩</div>
        <h1 className="text-2xl font-semibold text-[#d1d4dc]">Payment Cancelled</h1>
        <p className="mt-3 text-sm text-[#868993]">
          No worries — you can upgrade whenever you&apos;re ready.
          Your current plan remains active.
        </p>
        <div className="mt-8 flex flex-col gap-3">
          <Link
            className="rounded-lg bg-[#2962ff] px-6 py-2.5 text-sm font-medium text-white hover:bg-[#2962ff]/80 transition-colors"
            href="/billing"
          >
            View Plans
          </Link>
          <Link
            className="rounded-lg border border-[#2a2e39] px-6 py-2.5 text-sm text-[#868993] hover:text-[#d1d4dc] hover:border-[#d1d4dc] transition-colors"
            href="/"
          >
            Back to Dashboard
          </Link>
        </div>
      </div>
    </main>
  );
}
