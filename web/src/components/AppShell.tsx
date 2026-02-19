"use client";

import { usePathname } from "next/navigation";
import { useState } from "react";
import SidebarNav from "@/app/SidebarNav";
import { useI18n } from "@/lib/i18n";

export default function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const isLanding = pathname === "/";
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const { t } = useI18n();

  if (isLanding) {
    return <div className="min-w-0 flex-1">{children}</div>;
  }

  return (
    <div className="flex w-full">
      <aside
        className={`hidden shrink-0 border-r border-[#2a2e39] bg-[#131722] transition-all duration-200 md:block ${
          sidebarOpen ? "w-56" : "w-12"
        }`}
      >
        <div className="sticky top-14 flex h-[calc(100vh-3.5rem)] flex-col">
          <button
            type="button"
            onClick={() => setSidebarOpen((v) => !v)}
            className="flex h-10 w-full items-center justify-center border-b border-[#2a2e39] text-[#868993] hover:bg-[#1e222d] hover:text-[#d1d4dc] transition-colors"
            aria-label={sidebarOpen ? t.sidebar.close : t.sidebar.open}
          >
            {sidebarOpen ? (
              <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 19l-7-7 7-7m8 14l-7-7 7-7" />
              </svg>
            ) : (
              <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 5l7 7-7 7M5 5l7 7-7 7" />
              </svg>
            )}
          </button>
          <div className="min-h-0 flex-1 overflow-y-auto">
            {sidebarOpen && (
              <SidebarNav />
            )}
          </div>
        </div>
      </aside>
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  );
}
