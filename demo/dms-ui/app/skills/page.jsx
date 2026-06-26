"use client";

import { useCallback, useState } from "react";
import useSWR from "swr";
import AppShell from "../../components/AppShell";
import {
  ApiOfflineError,
  deactivateSkill,
  fetchSkills,
} from "../../lib/api";

function CaptureStatus({ enabled }) {
  const on = enabled === true;
  return (
    <div
      className="cx-gate-verdict"
      style={{
        background: on ? "#064e3b" : "#1f2937",
        borderColor: on ? "#10b981" : "#4b5563",
        marginBottom: 20,
      }}
    >
      <div className="cx-mono" style={{ color: on ? "#10b981" : "#9ca3af" }}>
        {on ? "RECORDING ON — consented skill capture enabled" : "RECORDING OFF — opt-in required (DMS_SKILL_CAPTURE_ENABLED)"}
      </div>
      <p className="cx-muted" style={{ fontSize: 12, marginTop: 8 }}>
        Captured skills stay on-box. Only successful gate-passed chains are recorded. Stewards can deactivate any skill.
      </p>
    </div>
  );
}

export default function SkillsPage() {
  const { data, isLoading, mutate } = useSWR("skills", fetchSkills, {
    refreshInterval: 8000,
  });
  const [status, setStatus] = useState("");
  const [busyId, setBusyId] = useState(null);

  const skills = data?.skills || [];
  const captureEnabled = data?.capture_enabled;

  const handleDeactivate = useCallback(async (skillId) => {
    setBusyId(skillId);
    setStatus("");
    try {
      await deactivateSkill(skillId);
      setStatus("Skill deactivated.");
      mutate();
    } catch (err) {
      setStatus(err instanceof ApiOfflineError ? "API offline." : err.message);
    } finally {
      setBusyId(null);
    }
  }, [mutate]);

  return (
    <AppShell loading={isLoading}>
      <div className="cx-label" style={{ marginBottom: 12 }}>
        CAPTURED SKILLS
      </div>
      <CaptureStatus enabled={captureEnabled} />
      {status && <p className="cx-muted" style={{ marginBottom: 12 }}>{status}</p>}

      {skills.length === 0 && !isLoading ? (
        <div className="cx-empty-state">
          No captured skills yet. Enable capture and complete a gate-passed task chain.
        </div>
      ) : (
        <table className="cx-audit-table">
          <thead>
            <tr>
              <th>INTENT</th>
              <th>TRIGGER</th>
              <th>TASK</th>
              <th>SUPPORT</th>
              <th>SUCCESS</th>
              <th>STATUS</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {skills.map((s) => (
              <tr key={s.id}>
                <td className="cx-mono">{s.intent || "—"}</td>
                <td>{s.trigger_pattern}</td>
                <td className="cx-mono">{s.task_id}</td>
                <td>{s.support_count}</td>
                <td>{s.success_count}</td>
                <td>{s.active ? "ACTIVE" : "OFF"}</td>
                <td>
                  {s.active ? (
                    <button
                      type="button"
                      className="cx-btn"
                      disabled={busyId === s.id}
                      onClick={() => handleDeactivate(s.id)}
                    >
                      DEACTIVATE
                    </button>
                  ) : (
                    "—"
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </AppShell>
  );
}
