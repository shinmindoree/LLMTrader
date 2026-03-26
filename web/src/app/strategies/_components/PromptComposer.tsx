"use client";

import type { PromptComposerProps } from "../_lib/helpers";

export function PromptComposer({
  centered = false,
  disabled = false,
  isSending,
  onChange,
  onCompositionEnd,
  onCompositionStart,
  onKeyDown,
  onSubmit,
  placeholder,
  prompt,
}: PromptComposerProps) {
  const busy = disabled || isSending;

  return (
    <div className={`w-full ${centered ? "max-w-3xl" : "max-w-4xl"}`}>
      <form
        className={`rounded-[28px] border border-[#343946] bg-[#2a2d35] shadow-[0_18px_50px_rgba(0,0,0,0.28)] ${
          centered ? "px-5 py-5" : "px-4 py-3"
        }`}
        onSubmit={onSubmit}
      >
        <div className="flex items-end gap-3">
          <textarea
            className={`flex-1 resize-none bg-transparent text-[15px] leading-7 text-[#ececf1] placeholder:text-[#8f96a3] focus:outline-none ${
              centered ? "min-h-[160px] px-1 py-1" : "min-h-[28px] max-h-[220px] px-1 py-2"
            }`}
            disabled={disabled}
            onChange={(e) => onChange(e.target.value)}
            onCompositionStart={onCompositionStart}
            onCompositionEnd={onCompositionEnd}
            onKeyDown={onKeyDown}
            placeholder={placeholder}
            rows={centered ? 7 : 1}
            value={prompt}
          />
          <button
            aria-label="Send message"
            className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-[#f4f4f4] text-[#111318] transition hover:bg-white disabled:cursor-not-allowed disabled:bg-[#3b404c] disabled:text-[#7b8393]"
            disabled={!prompt.trim() || busy}
            type="submit"
          >
            <svg
              aria-hidden="true"
              className="h-4 w-4"
              fill="none"
              viewBox="0 0 16 16"
              xmlns="http://www.w3.org/2000/svg"
            >
              <path
                d="M8 13V3M8 3L4.5 6.5M8 3l3.5 3.5"
                stroke="currentColor"
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth="1.6"
              />
            </svg>
          </button>
        </div>
      </form>
      <p className={`mt-3 text-xs text-[#8f96a3] ${centered ? "text-center" : ""}`}>
        Note: execution settings in the Backtest/Live forms override values mentioned in this
        prompt.
      </p>
    </div>
  );
}
