"use client";

export function DashboardSkeleton() {
  return (
    <div className="mx-auto w-full max-w-6xl px-4 py-4 animate-pulse">
      {/* Header */}
      <header className="mb-6">
        <div className="h-6 w-40 rounded bg-[#2a2e39]" />
        <div className="mt-2 h-4 w-64 rounded bg-[#2a2e39]" />
      </header>

      {/* Exchange connection bar */}
      <div className="flex items-center justify-between rounded-lg border border-[#2a2e39] bg-[#1e222d] px-4 py-3">
        <div className="flex items-center gap-2">
          <div className="h-4 w-16 rounded bg-[#31353f]" />
          <div className="h-4 w-20 rounded bg-[#31353f]" />
          <div className="h-5 w-24 rounded bg-[#31353f]" />
        </div>
        <div className="h-4 w-20 rounded bg-[#31353f]" />
      </div>

      {/* Stat cards grid */}
      <div className="mt-5 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {Array.from({ length: 3 }).map((_, i) => (
          <div
            key={i}
            className="flex flex-col rounded-lg border border-[#2a2e39] bg-[#1e222d] p-4"
          >
            <div className="flex items-center justify-between">
              <div className="h-3 w-24 rounded bg-[#31353f]" />
              <div className="h-4 w-4 rounded bg-[#31353f]" />
            </div>
            <div className="mt-3 h-8 w-12 rounded bg-[#2a2d35]" />
            <div className="mt-3 border-t border-[#2a2e39] pt-3">
              <div className="h-3 w-16 rounded bg-[#31353f]" />
              <div className="mt-2 h-3 w-32 rounded bg-[#2a2d35]" />
            </div>
          </div>
        ))}
      </div>

      {/* Asset overview placeholder */}
      <div className="mt-6 rounded-lg border border-[#2a2e39] bg-[#1e222d] p-4">
        <div className="h-5 w-32 rounded bg-[#31353f]" />
        <div className="mt-4 grid gap-3 sm:grid-cols-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="rounded bg-[#131722] p-3">
              <div className="h-3 w-20 rounded bg-[#2a2d35]" />
              <div className="mt-2 h-5 w-24 rounded bg-[#2a2d35]" />
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
