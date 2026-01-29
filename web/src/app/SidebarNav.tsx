"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const NAV_ITEMS: Array<{ href: string; label: string }> = [
  { href: "/", label: "Home" },
  { href: "/strategies", label: "Strategies" },
  { href: "/live", label: "Live" },
  { href: "/backtest", label: "Backtest" },
];

function isActive(pathname: string, href: string): boolean {
  if (href === "/") return pathname === "/";
  return pathname === href || pathname.startsWith(`${href}/`);
}

export default function SidebarNav() {
  const pathname = usePathname();

  return (
    <nav className="px-3 py-4 text-sm">
      <ul className="space-y-1">
        {NAV_ITEMS.map((item) => {
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
