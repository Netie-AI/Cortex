import { useEffect, useState } from "react";
import { fetchAudit } from "../lib/api";

export default function AuditPage() {
  const [entries, setEntries] = useState([]);

  useEffect(() => {
    const load = () => fetchAudit().then((d) => setEntries(d.entries || [])).catch(() => {});
    load();
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, []);

  return (
    <div className="card">
      <strong>Query audit log</strong>
      <table style={{ marginTop: "0.75rem" }}>
        <thead>
          <tr>
            <th>Time</th>
            <th>SQL</th>
            <th>Status</th>
            <th>Rows</th>
          </tr>
        </thead>
        <tbody>
          {entries.map((e, i) => (
            <tr key={i}>
              <td>{e.timestamp}</td>
              <td style={{ maxWidth: 360, overflow: "hidden", textOverflow: "ellipsis" }}>
                {e.safe_sql || e.original_sql}
              </td>
              <td>
                <span className={`badge ${e.passed ? "ok" : "blocked"}`}>
                  {e.passed ? "PASSED" : e.violations?.join(", ")}
                </span>
              </td>
              <td>{e.row_count ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {!entries.length && (
        <p style={{ color: "var(--muted)" }}>No queries yet — run some from Chat.</p>
      )}
    </div>
  );
}
