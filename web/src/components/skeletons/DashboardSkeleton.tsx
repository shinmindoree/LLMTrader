"use client";

export function DashboardSkeleton() {
  return (
    <div className="flex w-full animate-pulse px-4 py-4">
      <div className="hidden min-w-0 flex-1 xl:block" aria-hidden />
      <div className="mx-auto w-full max-w-6xl shrink-0">
        <header className="mb-6">
          <div className="h-6 w-40 rounded bg-[#2a2e39]" />
          <div className="mt-2 h-4 w-64 rounded bg-[#2a2e39]" />
        </header>

        <div className="flex items-center justify-between rounded-lg border border-[#2a2e39] bg-[#1e222d] px-4 py-3">
          <div className="flex items-center gap-2">
            <div className="h-4 w-16 rounded bg-[#31353f]" />
            <div className="h-4 w-20 rounded bg-[#31353f]" />
            <div className="h-5 w-24 rounded bg-[#31353f]" />
          </div>
          <div className="h-4 w-20 rounded bg-[#31353f]" />
        </div>

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

        <div className="mt-8 rounded-lg border border-[#2a2e39] bg-[#1e222d] p-4">
          <div className="mb-3 h-4 w-28 rounded bg-[#31353f]" />
          <div className="grid min-h-[200px] grid-cols-1 gap-3 lg:grid-cols-2">
            <div className="rounded border border-[#2a2e39] bg-[#131722] p-2">
              {Array.from({ length: 5 }).map((_, i) => (
                <div key={i} className="mb-2 h-10 rounded bg-[#2a2d35]" />
              ))}
            </div>
            <div className="rounded border border-[#2a2e39] bg-[#131722] p-4">
              <div className="h-4 w-full rounded bg-[#2a2d35]" />
              <div className="mt-2 h-3 w-3/4 rounded bg-[#2a2d35]" />
            </div>
          </div>
        </div>
      </div>

      <aside className="hidden h-[calc(100dvh-5rem)] w-80 shrink-0 border-l border-[#2a2e39] pl-3 xl:block">
        <div className="h-3 w-24 rounded bg-[#2a2e39]" />
        <div className="mt-4 space-y-2">
          {Array.from({ length: 8 }).map((_, i) => (
            <div key={i} className="h-8 rounded bg-[#1e222d]" />
          ))}
        </div>
      </aside>
    </div>
  );
}
