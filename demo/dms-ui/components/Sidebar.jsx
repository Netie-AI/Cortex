"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";

const WORK = [
  { href: "/", label: "Query", d: "M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" },
  { href: "/studio", label: "Studio", d: "M12 20h9M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z" },
  { href: "/warehouse", label: "Warehouse", d: "M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" },
  { href: "/brain", label: "Brain", d: "M12 2a7 7 0 0 0-4 12.7V22h8v-7.3A7 7 0 0 0 12 2z" },
  { href: "/chat", label: "Chat", d: "M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z" },
];

const COMPANY = [
  { href: "/data", label: "Data", d: "M4 7h16M4 12h16M4 17h10" },
  { href: "/skills", label: "Skills", d: "M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z" },
  { href: "/audit", label: "Activity", d: "M3 3v18h18M7 16l4-4 4 3 5-6" },
];

function Icon({ d }) {
  return (
    <svg className="cx-sidebar-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden>
      <path d={d} />
    </svg>
  );
}

export default function Sidebar() {
  const pathname = usePathname();
  const router = useRouter();
  const [collapsed, setCollapsed] = useState(false);

  useEffect(() => {
    const saved = localStorage.getItem("cx-rail") === "collapsed";
    setCollapsed(saved);
    document.documentElement.dataset.rail = saved ? "collapsed" : "";
  }, []);

  function toggle() {
    const next = !collapsed;
    setCollapsed(next);
    document.documentElement.dataset.rail = next ? "collapsed" : "";
    localStorage.setItem("cx-rail", next ? "collapsed" : "open");
  }

  return (
    <aside className="cx-sidebar">
      <div className="cx-sidebar-brand">
        <button type="button" className="cx-sidebar-collapse" onClick={toggle} title={collapsed ? "Expand sidebar" : "Collapse sidebar"}>
          {collapsed ? "]" : "["}
        </button>
        <div className="cx-sidebar-brand-copy">
          <p className="cx-sidebar-brand-title">Cortex</p>
          <p className="cx-sidebar-brand-sub">DMS warehouse</p>
        </div>
      </div>
      <nav className="cx-sidebar-nav">
        <div>
          <button type="button" className="cx-sidebar-new" onClick={() => router.push("/")}>
            <Icon d="M12 20h9M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z" />
            <span className="cx-sidebar-label">New query</span>
          </button>
          <button type="button" className="cx-sidebar-search" onClick={() => router.push("/")}>
            <Icon d="M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16zM21 21l-4.3-4.3" />
            <span className="cx-sidebar-label">Search</span>
            <span className="cx-sidebar-key">Ctrl K</span>
          </button>
        </div>
        <div>
          <div className="cx-sidebar-slot-label">Work</div>
          {WORK.map((item) => (
            <Link key={item.href} href={item.href} className={pathname === item.href ? "active" : ""}>
              <Icon d={item.d} />
              <span className="cx-sidebar-label">{item.label}</span>
            </Link>
          ))}
        </div>
        <div>
          <div className="cx-sidebar-slot-label">Company</div>
          {COMPANY.map((item) => (
            <Link key={item.href} href={item.href} className={pathname === item.href ? "active" : ""}>
              <Icon d={item.d} />
              <span className="cx-sidebar-label">{item.label}</span>
            </Link>
          ))}
        </div>
      </nav>
      <div className="cx-sidebar-version">demo · contract 1.2.0</div>
    </aside>
  );
}
