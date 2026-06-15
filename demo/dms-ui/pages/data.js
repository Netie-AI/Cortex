import { useEffect, useState } from "react";
import { fetchData, fetchChangelog } from "../lib/api";

function Table({ rows }) {
  if (!rows?.length) return <p>No rows.</p>;
  const cols = Object.keys(rows[0]);
  return (
    <table>
      <thead>
        <tr>
          {cols.map((c) => (
            <th key={c}>{c}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={i}>
            {cols.map((c) => (
              <td key={c}>{String(r[c] ?? "")}</td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function DataPage() {
  const [variant, setVariant] = useState("clean");
  const [rows, setRows] = useState([]);
  const [changelog, setChangelog] = useState([]);

  useEffect(() => {
    fetchData(variant).then((d) => setRows(d.rows)).catch(() => setRows([]));
  }, [variant]);

  useEffect(() => {
    fetchChangelog().then((d) => setChangelog(d.entries || [])).catch(() => {});
  }, []);

  return (
    <div>
      <div className="card">
        <strong>Data inspector</strong>
        <div style={{ marginTop: "0.75rem" }}>
          <button
            style={{
              marginRight: "0.5rem",
              background: variant === "messy" ? "var(--accent)" : "#1a2330",
            }}
            onClick={() => setVariant("messy")}
          >
            Messy
          </button>
          <button
            style={{
              background: variant === "clean" ? "var(--accent)" : "#1a2330",
            }}
            onClick={() => setVariant("clean")}
          >
            Clean
          </button>
        </div>
      </div>
      <div className="card">
        <Table rows={rows} />
      </div>
      <div className="card">
        <strong>Changelog</strong> ({changelog.length} entries)
        <table style={{ marginTop: "0.5rem" }}>
          <thead>
            <tr>
              <th>Rule</th>
              <th>Col</th>
              <th>Old</th>
              <th>New</th>
            </tr>
          </thead>
          <tbody>
            {changelog.slice(0, 30).map((e, i) => (
              <tr key={i}>
                <td>{e.rule_id}</td>
                <td>{e.col}</td>
                <td>{String(e.old_value)}</td>
                <td>{String(e.new_value)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
