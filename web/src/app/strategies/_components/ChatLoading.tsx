"use client";

export function PendingReply() {
  return (
    <div className="inline-flex items-center gap-1.5 rounded-full bg-[#2a2d35] px-4 py-3">
      {[0, 1, 2].map((idx) => (
        <span
          key={`pending-dot-${idx}`}
          className="h-2 w-2 rounded-full bg-[#8f96a3] animate-pulse"
          style={{ animationDelay: `${idx * 160}ms` }}
        />
      ))}
    </div>
  );
}

export function ChatPanelLoading() {
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex-1 px-6 py-6">
        <div className="mx-auto flex w-full max-w-4xl flex-col gap-4">
          {Array.from({ length: 3 }).map((_, idx) => (
            <div
              key={`chat-loading-${idx}`}
              className="animate-pulse rounded-[24px] border border-[#2f3440] bg-[#1f232b] p-5"
            >
              <div className="h-4 w-24 rounded bg-[#31353f]" />
              <div className="mt-4 h-3 w-full rounded bg-[#2a2d35]" />
              <div className="mt-2 h-3 w-5/6 rounded bg-[#2a2d35]" />
              <div className="mt-2 h-3 w-2/3 rounded bg-[#2a2d35]" />
            </div>
          ))}
        </div>
      </div>
      <div className="shrink-0 border-t border-[#2a2e39] px-6 py-5">
        <div className="mx-auto w-full max-w-4xl animate-pulse rounded-[28px] border border-[#343946] bg-[#2a2d35] px-5 py-5">
          <div className="h-4 w-40 rounded bg-[#343946]" />
          <div className="mt-3 h-4 w-3/4 rounded bg-[#31353f]" />
        </div>
      </div>
    </div>
  );
}
