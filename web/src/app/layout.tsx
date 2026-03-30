import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import NextTopLoader from "nextjs-toploader";
import { Toaster } from "sonner";
import "./globals.css";
import AppShell from "@/components/AppShell";
import { Header } from "@/components/Header";
import { Providers } from "@/components/Providers";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

const siteUrl = process.env.NEXT_PUBLIC_SITE_URL ?? "https://alphaweaver.com";

export const metadata: Metadata = {
  title: {
    default: "AlphaWeaver | Describe. Backtest. Trade.",
    template: "%s | AlphaWeaver",
  },
  description: "AI-powered Binance Futures trading platform. Describe strategies in natural language, backtest, and trade live.",
  metadataBase: new URL(siteUrl),
  openGraph: {
    title: "AlphaWeaver | AI Trading for Binance Futures",
    description: "AI-powered Binance Futures trading platform. Describe strategies in natural language, backtest, and trade live.",
    url: siteUrl,
    siteName: "AlphaWeaver",
    type: "website",
    locale: "en_US",
  },
  twitter: {
    card: "summary_large_image",
    title: "AlphaWeaver | AI Trading for Binance Futures",
    description: "AI-powered Binance Futures trading platform. Describe strategies in natural language, backtest, and trade live.",
  },
  robots: {
    index: true,
    follow: true,
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className={`${geistSans.variable} ${geistMono.variable} antialiased`}>
        <NextTopLoader color="#2962ff" height={3} showSpinner={false} />
        <Providers>
        <Header />
        <AppShell>{children}</AppShell>
        <Toaster
          theme="system"
          position="bottom-right"
          toastOptions={{
            style: {
              background: "var(--card-bg)",
              border: "1px solid var(--border)",
              color: "var(--foreground)",
            },
          }}
        />
        </Providers>
      </body>
    </html>
  );
}
