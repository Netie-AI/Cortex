"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const NAV = [
  { href: "/", label: "QUERY" },
  { href: "/warehouse", label: "WAREHOUSE" },
  { href: "/brain", label: "BRAIN" },
  { href: "/chat", label: "CHAT" },
  { href: "/data", label: "DATA" },
  { href: "/audit", label: "AUDIT" },
];

export default function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="cx-sidebar">
      <div className="cx-sidebar-brand">
        <p className="cx-sidebar-brand-title">CORTEX OS</p>
        <p className="cx-sidebar-brand-sub">DMS · WAREHOUSE</p>
      </div>
      <nav className="cx-sidebar-nav">
        <div className="cx-sidebar-slot-label">OPERATIONS</div>
        {NAV.map(({ href, label }) => (
          <Link
            key={href}
            href={href}
            className={pathname === href ? "active" : ""}
          >
            {label}
          </Link>
        ))}
      </nav>
      <div className="cx-sidebar-version">v0.1.0-demo</div>
    </aside>
  );
}
