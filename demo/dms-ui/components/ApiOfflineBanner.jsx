"use client";

export default function ApiOfflineBanner({ show }) {
  if (!show) return null;
  return (
    <div className="cx-offline-banner">
      API offline — run .\demo\run_demo.ps1 to start the backend
    </div>
  );
}
