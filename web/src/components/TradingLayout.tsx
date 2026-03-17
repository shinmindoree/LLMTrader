"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { TradingViewChart } from "@/components/TradingViewChart";
import { TradingTabs } from "@/components/TradingTabs";

const DEFAULT_CHART_RATIO = 0.45;
const MIN_TAB_HEIGHT = 80;

export function TradingLayout({ children }: { children: React.ReactNode }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [splitY, setSplitY] = useState<number | null>(null);
  const isDraggingRef = useRef(false);
  const [isDragging, setIsDragging] = useState(false);

  useEffect(() => {
    if (containerRef.current && splitY === null) {
      setSplitY(containerRef.current.clientHeight * DEFAULT_CHART_RATIO);
    }
  }, [splitY]);

  const handlePointerDown = useCallback((e: React.PointerEvent) => {
    e.preventDefault();
    (e.target as HTMLElement).setPointerCapture(e.pointerId);
    isDraggingRef.current = true;
    setIsDragging(true);
  }, []);

  useEffect(() => {
    if (!isDragging) return;

    const onMove = (e: PointerEvent) => {
      if (!containerRef.current || !isDraggingRef.current) return;
      const rect = containerRef.current.getBoundingClientRect();
      const y = e.clientY - rect.top;
      setSplitY(Math.max(0, Math.min(y, rect.height - MIN_TAB_HEIGHT)));
    };

    const onUp = () => {
      isDraggingRef.current = false;
      setIsDragging(false);
    };

    document.addEventListener("pointermove", onMove);
    document.addEventListener("pointerup", onUp);
    return () => {
      document.removeEventListener("pointermove", onMove);
      document.removeEventListener("pointerup", onUp);
    };
  }, [isDragging]);

  const currentSplitY = splitY ?? 300;

  return (
    <div
      ref={containerRef}
      className="relative h-[calc(100vh-3.5rem)] overflow-hidden bg-[#131722]"
    >
      <div className="absolute inset-0">
        <TradingViewChart />
      </div>

      <div
        className="absolute left-0 right-0 bottom-0 z-10 flex flex-col"
        style={{ top: `${currentSplitY}px` }}
      >
        <div
          onPointerDown={handlePointerDown}
          className={[
            "flex h-1.5 shrink-0 cursor-row-resize items-center justify-center border-t border-[#2a2e39] touch-none select-none",
            isDragging
              ? "bg-[#2962ff]/20"
              : "bg-[#1e222d] hover:bg-[#252936]",
          ].join(" ")}
        >
          <div className="h-0.5 w-8 rounded-full bg-[#868993]" />
        </div>

        <TradingTabs />
        <div className="flex-1 overflow-y-auto bg-[#131722]">{children}</div>
      </div>

      {isDragging && (
        <div className="fixed inset-0 z-50 cursor-row-resize" />
      )}
    </div>
  );
}
