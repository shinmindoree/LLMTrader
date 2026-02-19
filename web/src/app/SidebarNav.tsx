"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

type NavItem = { href: string; label: string; section?: string };

const NAV_ITEMS: NavItem[] = [
  { href: "/dashboard", label: "Dashboard" },
  { href: "/strategies", label: "Strategies" },
  { href: "/backtest", label: "Backtest" },
  { href: "/live", label: "Live" },
  { href: "/settings", label: "Settings", section: "account" },
  { href: "/billing", label: "Billing", section: "account" },
  { href: "/admin", label: "Admin", section: "account" },
];

function isActive(pathname: string, href: string): boolean {
  if (href === "/dashboard") return pathname === "/dashboard";
  return pathname === href || pathname.startsWith(`${href}/`);
}

export default function SidebarNav() {
  const pathname = usePathname();

  const mainItems = NAV_ITEMS.filter((i) => !i.section);
  const accountItems = NAV_ITEMS.filter((i) => i.section === "account");

  return (
    <nav className="px-3 py-4 text-sm">
      <ul className="space-y-1">
        {mainItems.map((item) => {
          const active = isActive(pathname, item.href);
          return (
            <li key={item.href}>
              <Link
                className={[
                  "block rounded px-3 py-2 transition-colors",
                  active
                    ? "bg-[#2962ff] text-white"
                    : "text-[#868993] hover:bg-[#1e222d] hover:text-[#d1d4dc]",
                ].join(" ")}
                href={item.href}
              >
                {item.label}
              </Link>
            </li>
          );
        })}
      </ul>
      <div className="mt-4 mb-2 border-t border-[#2a2e39]" />
      <div className="px-3 py-1 text-xs text-[#868993] uppercase tracking-wider">Account</div>
      <ul className="space-y-1">
        {accountItems.map((item) => {
          const active = isActive(pathname, item.href);
          return (
            <li key={item.href}>
              <Link
                className={[
                  "block rounded px-3 py-2 transition-colors",
                  active
                    ? "bg-[#2962ff] text-white"
                    : "text-[#868993] hover:bg-[#1e222d] hover:text-[#d1d4dc]",
                ].join(" ")}
                href={item.href}
              >
                {item.label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
