"use client";

import { usePathname } from "next/navigation";
import SidebarNav from "@/app/SidebarNav";

export default function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const isLanding = pathname === "/";

  if (isLanding) {
    return <div className="min-w-0 flex-1">{children}</div>;
  }

  return (
    <div className="flex w-full">
      <aside className="hidden w-56 shrink-0 border-r border-[#2a2e39] bg-[#131722] md:block">
        <div className="sticky top-14 h-[calc(100vh-3.5rem)] overflow-y-auto">
          <SidebarNav />
        </div>
      </aside>
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  );
}
