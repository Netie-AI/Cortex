"use client";

import { useCallback, useState } from "react";
import useSWR from "swr";
import AppShell from "../../components/AppShell";
import {
  ApiOfflineError,
  createChatThread,
  fetchThreadMessages,
  sendThreadMessage,
} from "../../lib/api";

function threadLabel(thread) {
  return thread.customer_label || thread.external_ref || thread.id.slice(0, 8);
}

export default function ChatPage() {
  const [threads, setThreads] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [newLabel, setNewLabel] = useState("");
  const [newRef, setNewRef] = useState("");
  const [sender, setSender] = useState("customer");
  const [body, setBody] = useState("");
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);

  const { data, mutate, isLoading } = useSWR(
    selectedId ? `chat-messages-${selectedId}` : null,
    () => fetchThreadMessages(selectedId),
    { refreshInterval: 5000 }
  );

  const messages = data?.messages || [];
  const selected = threads.find((t) => t.id === selectedId) || null;

  const handleCreateThread = useCallback(async () => {
    setBusy(true);
    setStatus("");
    try {
      const result = await createChatThread({
        customer_label: newLabel || undefined,
        external_ref: newRef || undefined,
      });
      const thread = result.thread;
      setThreads((prev) => [thread, ...prev.filter((t) => t.id !== thread.id)]);
      setSelectedId(thread.id);
      setNewLabel("");
      setNewRef("");
      setStatus("Thread created.");
    } catch (err) {
      setStatus(err instanceof ApiOfflineError ? "API offline." : err.message);
    } finally {
      setBusy(false);
    }
  }, [newLabel, newRef]);

  const handleSend = useCallback(async () => {
    if (!selectedId || !body.trim()) return;
    setBusy(true);
    setStatus("");
    try {
      await sendThreadMessage(selectedId, { sender, body: body.trim() });
      setBody("");
      await mutate();
      setStatus("Message sent.");
    } catch (err) {
      setStatus(err instanceof ApiOfflineError ? "API offline." : err.message);
    } finally {
      setBusy(false);
    }
  }, [selectedId, sender, body, mutate]);

  return (
    <AppShell loading={false}>
      <div className="cx-chat-layout">
        <section className="cx-chat-threads">
          <div className="cx-label" style={{ marginBottom: 12 }}>
            THREADS
          </div>
          <div className="cx-chat-new">
            <input
              className="cx-input"
              placeholder="Customer label"
              value={newLabel}
              onChange={(e) => setNewLabel(e.target.value)}
            />
            <input
              className="cx-input"
              placeholder="External ref (optional)"
              value={newRef}
              onChange={(e) => setNewRef(e.target.value)}
            />
            <button
              type="button"
              className="cx-btn cx-btn-primary"
              disabled={busy}
              onClick={handleCreateThread}
            >
              NEW THREAD
            </button>
          </div>
          <div className="cx-chat-thread-list">
            {threads.length === 0 ? (
              <p className="cx-muted">No threads yet. Create one to start.</p>
            ) : (
              threads.map((thread) => (
                <button
                  key={thread.id}
                  type="button"
                  className={`cx-chat-thread-item ${selectedId === thread.id ? "active" : ""}`}
                  onClick={() => setSelectedId(thread.id)}
                >
                  <span className="cx-mono">{threadLabel(thread)}</span>
                  <span className="cx-muted">{thread.status}</span>
                </button>
              ))
            )}
          </div>
        </section>

        <section className="cx-chat-pane">
          {!selected ? (
            <p className="cx-muted">Select a thread to view messages.</p>
          ) : (
            <>
              <div className="cx-label" style={{ marginBottom: 8 }}>
                {threadLabel(selected).toUpperCase()}
              </div>
              <div className="cx-chat-messages">
                {isLoading && messages.length === 0 ? (
                  <p className="cx-muted">Loading messages…</p>
                ) : messages.length === 0 ? (
                  <p className="cx-muted">No messages yet.</p>
                ) : (
                  messages.map((msg) => (
                    <div key={msg.id} className="cx-chat-message">
                      <div className="cx-chat-message-meta">
                        <span className="cx-mono">{msg.direction.toUpperCase()}</span>
                        <span className="cx-mono">{msg.sender}</span>
                        <span className="cx-muted">{msg.created_at}</span>
                      </div>
                      <pre className="cx-chat-message-body">{msg.body}</pre>
                    </div>
                  ))
                )}
              </div>
              <div className="cx-chat-compose">
                <input
                  className="cx-input"
                  placeholder="Sender"
                  value={sender}
                  onChange={(e) => setSender(e.target.value)}
                />
                <textarea
                  className="cx-input cx-chat-textarea"
                  placeholder="Inbound message body"
                  value={body}
                  onChange={(e) => setBody(e.target.value)}
                  rows={3}
                />
                <button
                  type="button"
                  className="cx-btn cx-btn-primary"
                  disabled={busy || !body.trim()}
                  onClick={handleSend}
                >
                  SEND INBOUND
                </button>
              </div>
            </>
          )}
          {status ? <p className="cx-muted" style={{ marginTop: 12 }}>{status}</p> : null}
        </section>
      </div>
    </AppShell>
  );
}
