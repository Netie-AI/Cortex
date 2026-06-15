import "../styles/globals.css";
import Link from "next/link";

export default function App({ Component, pageProps }) {
  return (
    <div className="layout">
      <aside className="sidebar">
        <h1>DMS Brain Demo</h1>
        <p style={{ color: "var(--muted)", fontSize: "0.8rem" }}>
          Cortex OS · warehouse pack
        </p>
        <nav style={{ marginTop: "1.5rem" }}>
          <Link href="/">Chat</Link>
          <Link href="/data">Data inspector</Link>
          <Link href="/audit">Audit log</Link>
        </nav>
      </aside>
      <main className="main">
        <Component {...pageProps} />
      </main>
    </div>
  );
}
