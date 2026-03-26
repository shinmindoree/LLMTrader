"use client";

function TableRowSkeleton() {
  return (
    <div className="flex items-center gap-3 border-b border-[#2a2e39] px-4 py-3">
      <div className="h-4 w-4 rounded bg-[#31353f]" />
      <div className="h-4 w-36 rounded bg-[#2a2d35]" />
      <div className="ml-auto flex gap-4">
        <div className="h-4 w-16 rounded bg-[#31353f]" />
        <div className="h-4 w-20 rounded bg-[#31353f]" />
        <div className="h-4 w-14 rounded bg-[#31353f]" />
      </div>
    </div>
  );
}

export function BacktestSkeleton() {
  return (
    <main className="w-full px-4 py-3 animate-pulse">
      {/* Top bar */}
      <div className="flex items-center justify-between">
        <div className="h-4 w-48 rounded bg-[#2a2e39]" />
        <div className="flex gap-2">
          <div className="h-9 w-16 rounded border border-[#2a2e39] bg-[#1e222d]" />
          <div className="h-9 w-20 rounded border border-[#2a2e39] bg-[#1e222d]" />
          <div className="h-9 w-20 rounded border border-[#2a2e39] bg-[#1e222d]" />
        </div>
      </div>

      {/* New backtest button placeholder */}
      <section className="mt-4">
        <div className="w-full rounded-lg border-2 border-dashed border-[#2a2e39] bg-[#1e222d]/50 py-5">
          <div className="mx-auto h-4 w-32 rounded bg-[#31353f]" />
        </div>
      </section>

      {/* Run history */}
      <section className="mt-6">
        <div className="mb-3 h-4 w-24 rounded bg-[#31353f]" />
        <div className="rounded border border-[#2a2e39] bg-[#1e222d]">
          {Array.from({ length: 5 }).map((_, i) => (
            <TableRowSkeleton key={i} />
          ))}
        </div>
      </section>
    </main>
  );
}
