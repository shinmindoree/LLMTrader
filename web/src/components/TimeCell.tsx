"use client";

/**
 * Click-to-toggle Binance-style time cell.
 *
 * Renders an epoch-ms timestamp as `YYYY-MM-DD HH:mm:ss`. Default zone
 * is KST (matches the user's locale). Clicking the cell flips the global
 * preference between KST and UTC; the change is broadcast to every other
 * TimeCell on the page so they all switch together. The current zone
 * shows as a subtle suffix badge, and the alternate zone is provided as
 * the native tooltip for quick reference.
 */

import { formatBinanceTimeAny, useTimezone, type Timezone } from "@/lib/timeFormat";

interface TimeCellProps {
  /**
   * Timestamp value: epoch milliseconds, ISO/RFC string, or null/undefined.
   * null/undefined/non-finite values render as `-` and are not clickable.
   */
  value: number | string | null | undefined;
  /** Optional className for layout/typography composition. */
  className?: string;
  /** Hide the small `KST`/`UTC` suffix badge (default: visible). */
  hideZoneBadge?: boolean;
}

export function TimeCell({ value, className, hideZoneBadge }: TimeCellProps) {
  const { tz, toggle } = useTimezone();
  const other: Timezone = tz === "KST" ? "UTC" : "KST";
  const main = formatBinanceTimeAny(value, tz);
  const alt = formatBinanceTimeAny(value, other);
  const empty = main === "-";

  return (
    <span
      className={`inline-flex items-center gap-1.5 ${empty ? "" : "cursor-pointer select-none"} ${
        className ?? ""
      }`}
      role={empty ? undefined : "button"}
      tabIndex={empty ? undefined : 0}
      title={empty ? undefined : `${alt} (${other}) — click to switch`}
      onClick={
        empty
          ? undefined
          : (ev) => {
              ev.stopPropagation();
              toggle();
            }
      }
      onKeyDown={
        empty
          ? undefined
          : (ev) => {
              if (ev.key === "Enter" || ev.key === " ") {
                ev.preventDefault();
                ev.stopPropagation();
                toggle();
              }
            }
      }
    >
      <span>{main}</span>
      {!empty && !hideZoneBadge ? (
        <span className="rounded bg-[#2a2e39] px-1 py-px text-[9px] font-medium tracking-wide text-[#868993]">
          {tz}
        </span>
      ) : null}
    </span>
  );
}
