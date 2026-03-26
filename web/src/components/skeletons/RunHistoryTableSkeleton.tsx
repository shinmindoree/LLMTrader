"use client";

export function RunHistoryTableSkeleton({ rows = 5 }: { rows?: number }) {
  return (
    <div className="animate-pulse rounded border border-[#2a2e39] bg-[#1e222d]">
      {Array.from({ length: rows }).map((_, i) => (
        <div
          key={i}
          className="flex items-center gap-3 border-b border-[#2a2e39] px-4 py-3 last:border-b-0"
        >
          <div className="h-4 w-4 rounded bg-[#31353f]" />
          <div className="h-4 w-36 rounded bg-[#2a2d35]" />
          <div className="ml-auto flex gap-4">
            <div className="h-4 w-16 rounded bg-[#31353f]" />
            <div className="h-4 w-20 rounded bg-[#31353f]" />
            <div className="h-4 w-14 rounded bg-[#31353f]" />
          </div>
        </div>
      ))}
    </div>
  );
}
