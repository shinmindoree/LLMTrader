"use client";

import { usePathname } from "next/navigation";
import { TradingLayout } from "@/components/TradingLayout";

const TRADING_PATHS = ["/dashboard", "/strategies", "/backtest", "/live"];

function isTradingPage(pathname: string): boolean {
  return TRADING_PATHS.some(
    (p) => pathname === p || pathname.startsWith(`${p}/`),
  );
}

export default function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();

  if (pathname === "/") {
    return <div className="min-w-0 flex-1">{children}</div>;
  }

  if (isTradingPage(pathname)) {
    return <TradingLayout>{children}</TradingLayout>;
  }

  return <div className="min-w-0 flex-1">{children}</div>;
}
