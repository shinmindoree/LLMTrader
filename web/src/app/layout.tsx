import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import Image from "next/image";
import Link from "next/link";
import "./globals.css";
import SidebarNav from "./SidebarNav";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "LLMTrader",
  description: "LLMTrader local control panel",
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
          <div className="mx-auto flex h-14 max-w-6xl items-center justify-between px-4">
            <Link
              aria-label="Go to home"
              className="flex items-center gap-2 rounded px-2 py-1 hover:bg-[#2a2e39] transition-colors"
              href="/"
            >
              <Image alt="LLMTrader" height={24} priority src="/llmtrader-logo.svg" width={24} />
              <span className="text-sm font-semibold text-[#d1d4dc]">LLMTrader</span>
            </Link>
            <nav className="flex items-center gap-2 text-sm md:hidden">
              <Link className="rounded px-2 py-1 text-[#d1d4dc] hover:bg-[#2a2e39] transition-colors" href="/live">
                Live
              </Link>
              <Link className="rounded px-2 py-1 text-[#d1d4dc] hover:bg-[#2a2e39] transition-colors" href="/backtest">
                Backtest
              </Link>
            </nav>
          </div>
        </header>
        <div className="mx-auto flex max-w-6xl">
          <aside className="hidden w-56 shrink-0 border-r border-[#2a2e39] bg-[#131722] md:block">
            <div className="sticky top-14 h-[calc(100vh-3.5rem)] overflow-y-auto">
              <SidebarNav />
            </div>
          </aside>
          <div className="min-w-0 flex-1">{children}</div>
        </div>
      </body>
    </html>
  );
}
