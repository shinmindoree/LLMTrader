import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import Image from "next/image";
import Link from "next/link";
import "./globals.css";
import AppShell from "@/components/AppShell";
import { AuthActions } from "@/components/AuthActions";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "LLMTrader | AI-Powered Crypto Trading",
  description: "Binance futures backtest & live trading with strategy automation. Test strategies on testnet, deploy to mainnet.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className={`${geistSans.variable} ${geistMono.variable} antialiased bg-[#131722]`}>
        <header className="sticky top-0 z-50 h-14 border-b border-[#2a2e39] bg-[#1e222d]/90 backdrop-blur">
          <div className="flex h-14 w-full items-center justify-between px-4">
            <Link
              aria-label="Go to homepage"
              className="flex items-center gap-2 rounded px-2 py-1 hover:bg-[#2a2e39] transition-colors"
              href="/"
            >
              <Image alt="LLMTrader" height={24} priority src="/llmtrader-logo.svg" width={24} />
              <span className="text-sm font-semibold text-[#d1d4dc]">LLMTrader</span>
            </Link>
            <nav className="flex items-center gap-2 text-sm">
              <Link className="rounded px-2 py-1 text-[#d1d4dc] hover:bg-[#2a2e39] transition-colors" href="/#features">
                Features
              </Link>
              <Link className="rounded px-2 py-1 text-[#d1d4dc] hover:bg-[#2a2e39] transition-colors" href="/dashboard">
                Dashboard
              </Link>
              <Link className="rounded px-2 py-1 text-[#d1d4dc] hover:bg-[#2a2e39] transition-colors md:hidden" href="/live">
                Live
              </Link>
              <Link className="rounded px-2 py-1 text-[#d1d4dc] hover:bg-[#2a2e39] transition-colors md:hidden" href="/backtest">
                Backtest
              </Link>
              <Link className="rounded px-2 py-1 text-[#d1d4dc] hover:bg-[#2a2e39] transition-colors md:hidden" href="/settings">
                Settings
              </Link>
            </nav>
            <AuthActions />
          </div>
        </header>
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}
