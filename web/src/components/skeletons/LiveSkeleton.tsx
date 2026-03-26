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

export function LiveSkeleton() {
  return (
    <main className="w-full px-4 py-3 animate-pulse">
      {/* Top bar */}
      <div className="flex items-center justify-between">
        <div className="h-4 w-40 rounded bg-[#2a2e39]" />
        <div className="flex gap-2">
          <div className="h-9 w-16 rounded border border-[#2a2e39] bg-[#1e222d]" />
          <div className="h-9 w-20 rounded border border-[#2a2e39] bg-[#1e222d]" />
          <div className="h-9 w-20 rounded border border-[#2a2e39] bg-[#1e222d]" />
        </div>
      </div>

      {/* Active strategies placeholder */}
      <section className="mt-4">
        <div className="mb-2 flex items-center gap-2">
          <div className="h-4 w-28 rounded bg-[#31353f]" />
          <div className="h-5 w-10 rounded bg-[#2962ff]/20" />
        </div>
        <div className="space-y-2">
          {Array.from({ length: 2 }).map((_, i) => (
            <div
              key={i}
              className="flex items-center justify-between rounded-lg border border-[#2a2e39] bg-[#1e222d] px-4 py-3"
            >
              <div className="flex items-center gap-3">
                <div className="h-2 w-2 rounded-full bg-[#31353f]" />
                <div className="h-4 w-40 rounded bg-[#2a2d35]" />
              </div>
              <div className="h-7 w-14 rounded bg-[#31353f]" />
            </div>
          ))}
        </div>
      </section>

      {/* New strategy button placeholder */}
      <section className="mt-4">
        <div className="w-full rounded-lg border-2 border-dashed border-[#2a2e39] bg-[#1e222d]/50 py-5">
          <div className="mx-auto h-4 w-40 rounded bg-[#31353f]" />
        </div>
      </section>

      {/* Run history */}
      <section className="mt-10">
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
