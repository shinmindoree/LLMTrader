"use client";

import { useEffect, useState } from "react";

// MCAPS 정책: 매일 자정 KST 부근에 PostgreSQL 자동 stop → 자동 start.
// 관측된 다운타임: 00:06 ~ 00:28 KST (최대 ~18분).
const KST_OFFSET_MIN = 9 * 60;
const UPCOMING_START_MIN = 23 * 60 + 55; // 23:55 KST
const DOWNTIME_START_MIN = 0;            // 00:00 KST
const DOWNTIME_END_MIN = 30;             // 00:30 KST

type Phase = "none" | "upcoming" | "down";

function currentKstMinutes(now: Date): number {
  const utcMin = now.getUTCHours() * 60 + now.getUTCMinutes();
  return (utcMin + KST_OFFSET_MIN) % (24 * 60);
}

function computePhase(now: Date): Phase {
  const m = currentKstMinutes(now);
  if (m >= DOWNTIME_START_MIN && m < DOWNTIME_END_MIN) return "down";
  if (m >= UPCOMING_START_MIN) return "upcoming";
  return "none";
}

export function MaintenanceBanner() {
  const [phase, setPhase] = useState<Phase>("none");
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    const tick = () => setPhase(computePhase(new Date()));
    tick();
    const id = setInterval(tick, 30_000);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    if (phase === "none") setDismissed(false);
  }, [phase]);

  if (phase === "none" || dismissed) return null;

  const isDown = phase === "down";
  const bg = isDown ? "#3a1d1d" : "#3a2f1d";
  const border = isDown ? "#7a2e2e" : "#7a5a1d";
  const fg = isDown ? "#fca5a5" : "#fcd34d";
  const message = isDown
    ? "데이터 조회 점검 중입니다 (~00:30 KST). 라이브 매매는 정상 동작합니다."
    : "곧 데이터 조회 점검이 예정되어 있습니다 (00:00 ~ 00:30 KST). 라이브 매매에는 영향이 없습니다.";

  return (
    <div
      role="status"
      aria-live="polite"
      className="flex items-center justify-between gap-3 border-b px-4 py-2 text-sm"
      style={{ backgroundColor: bg, borderColor: border, color: fg }}
    >
      <div className="flex items-center gap-2">
        <span aria-hidden>{isDown ? "🔴" : "⚠️"}</span>
        <span>{message}</span>
      </div>
      <button
        type="button"
        onClick={() => setDismissed(true)}
        aria-label="닫기"
        className="text-xs opacity-70 hover:opacity-100"
        style={{ color: fg }}
      >
        ✕
      </button>
    </div>
  );
}
