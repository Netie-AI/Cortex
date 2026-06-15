"use client";

import { createContext, useContext, useState } from "react";

export const ROLES = {
  ANALYST: {
    id: "ANALYST",
    label: "ANALYST (read-only)",
    canApprove: false,
    isAdmin: false,
  },
  STEWARD: {
    id: "STEWARD",
    label: "DATA STEWARD",
    canApprove: true,
    isAdmin: false,
  },
  ADMIN: {
    id: "ADMIN",
    label: "ADMIN",
    canApprove: true,
    isAdmin: true,
  },
};

const RoleContext = createContext(null);

export function RoleProvider({ children }) {
  const [role, setRole] = useState(ROLES.ANALYST);
  return (
    <RoleContext.Provider value={{ role, setRole }}>
      {children}
    </RoleContext.Provider>
  );
}

export function useRole() {
  const ctx = useContext(RoleContext);
  if (!ctx) throw new Error("useRole requires RoleProvider");
  return ctx;
}
