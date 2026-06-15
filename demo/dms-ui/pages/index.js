import { useEffect, useState } from "react";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import { postQuery, fetchData } from "../lib/api";

function Chart({ spec }) {
  if (!spec || !spec.data?.length) return null;
  return (
    <div className="card" style={{ height: 280 }}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={spec.data}>
          <XAxis dataKey={spec.nameKey} tick={{ fill: "#8b9cb3", fontSize: 10 }} />
          <YAxis tick={{ fill: "#8b9cb3", fontSize: 10 }} />
          <Tooltip
            contentStyle={{ background: "#121820", border: "1px solid #1e2a38" }}
          />
          <Bar dataKey={spec.dataKey} fill="#3d8bfd" />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

export default function ChatPage() {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [meta, setMeta] = useState({ rowCount: "—", cleaned: "—" });
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    fetchData("clean")
      .then((d) =>
        setMeta({ rowCount: d.count, cleaned: new Date().toLocaleString() })
      )
      .catch(() => {});
  }, []);

  async function send() {
    if (!input.trim()) return;
    const q = input.trim();
    setInput("");
    setLoading(true);
    try {
      const res = await postQuery(q);
      setMessages((m) => [...m, { question: q, ...res }]);
    } catch (e) {
      setMessages((m) => [
        ...m,
        { question: q, answer: String(e), violations_blocked: ["API_ERROR"] },
      ]);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div>
      <div className="card">
        <strong>Warehouse inventory</strong>
        <div style={{ color: "var(--muted)", fontSize: "0.85rem" }}>
          Rows (clean sample): {meta.rowCount} · Last cleaned: {meta.cleaned}
        </div>
      </div>

      <div className="chat-log">
        {messages.map((m, i) => (
          <div key={i} className="card">
            <div>
              <strong>You:</strong> {m.question}
            </div>
            {m.violations_blocked?.length > 0 && (
              <div className="banner-danger">
                Query blocked: {m.violations_blocked.join(", ")}
              </div>
            )}
            <div style={{ marginTop: "0.5rem" }}>
              <strong>Answer:</strong> {m.answer}
            </div>
            <Chart spec={m.chart_spec} />
            {m.sql_used && (
              <details className="collapsible">
                <summary>SQL executed</summary>
                <pre>{m.sql_used}</pre>
              </details>
            )}
            {m.audit_id && (
              <details className="collapsible">
                <summary>Audit trail</summary>
                <pre>{JSON.stringify(m.audit || { audit_id: m.audit_id }, null, 2)}</pre>
              </details>
            )}
          </div>
        ))}
      </div>

      <div className="chat-input">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && send()}
          placeholder='Try: "Which SKUs are below reorder level in WH-A?"'
        />
        <button onClick={send} disabled={loading}>
          {loading ? "…" : "Ask"}
        </button>
      </div>
    </div>
  );
}
