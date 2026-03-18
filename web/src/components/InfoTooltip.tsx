"use client";

import { useState, useRef, useEffect } from "react";

export function InfoTooltip({ text }: { text: string }) {
  const [visible, setVisible] = useState(false);
  const [position, setPosition] = useState<"bottom" | "top">("bottom");
  const triggerRef = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    if (!visible || !triggerRef.current) return;
    const rect = triggerRef.current.getBoundingClientRect();
    const spaceBelow = window.innerHeight - rect.bottom;
    setPosition(spaceBelow < 120 ? "top" : "bottom");
  }, [visible]);

  return (
    <span
      ref={triggerRef}
      className="relative ml-1 inline-flex cursor-help"
      onMouseEnter={() => setVisible(true)}
      onMouseLeave={() => setVisible(false)}
    >
      <span className="inline-flex h-3.5 w-3.5 items-center justify-center rounded-full border border-[#868993]/50 text-[9px] font-semibold leading-none text-[#868993] transition-colors hover:border-[#d1d4dc] hover:text-[#d1d4dc]">
        i
      </span>
      {visible && (
        <span
          className={`absolute left-1/2 z-50 w-56 -translate-x-1/2 rounded border border-[#2a2e39] bg-[#1e222d] px-3 py-2 text-xs font-normal leading-relaxed text-[#d1d4dc] shadow-lg ${
            position === "bottom" ? "top-full mt-1.5" : "bottom-full mb-1.5"
          }`}
        >
          {text}
        </span>
      )}
    </span>
  );
}
