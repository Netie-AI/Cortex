"use client";

import { usePathname } from "next/navigation";
import RoleSwitcher from "./RoleSwitcher";

const PAGE_NAMES = {
  "/": "Query",
  "/studio": "Studio",
  "/warehouse": "Warehouse",
  "/brain": "Brain",
  "/chat": "Chat",
  "/data": "Data",
  "/skills": "Skills",
  "/audit": "Activity",
};

export default function TopBar({ indexedRows = "—", apiOnline = true }) {
  const pathname = usePathname();
  const pageName = PAGE_NAMES[pathname] || "Query";

  return (
    <header className="cx-topbar">
      <span className="cx-page-title">{pageName}</span>
      <div className="cx-topbar-status">
        {apiOnline ? (
          <>
            <span className="cx-status-connected">
              <span className="cx-status-dot" />
              live · dms_demo.duckdb
            </span>
            <span className="cx-status-sep">·</span>
            <span className="cx-status-meta">{indexedRows} rows</span>
            <span className="cx-status-sep">·</span>
          </>
        ) : (
          <span className="cx-status-meta">engine down</span>
        )}
        <RoleSwitcher />
      </div>
    </header>
  );
}
