"use client";

import { useState } from "react";
import useSWR from "swr";
import AppShell from "../../components/AppShell";
import { ApiOfflineError, deactivateSkill, fetchSkills, fetchSkillCaptureConfig, setSkillCaptureConfig } from "../../lib/api";
import { useRole } from "../../context/RoleContext";

export default function SkillsPage() {
  const { role } = useRole();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const { data: config, mutate: mutateConfig } = useSWR("skill-config", fetchSkillCaptureConfig);
  const { data: skills, isLoading, mutate: mutateSkills } = useSWR("skills", () => fetchSkills(false));

  const canManage = role.canApprove;

  async function toggleCapture() {
    if (!canManage) return;
    setBusy(true);
    setError("");
    try {
      await setSkillCaptureConfig(!config?.capture_enabled);
      mutateConfig();
    } catch (e) {
      if (!(e instanceof ApiOfflineError)) setError(String(e.message || e));
    } finally {
      setBusy(false);
    }
  }

  async function onDeactivate(skillId) {
    if (!canManage) return;
    setBusy(true);
    setError("");
    try {
      await deactivateSkill(skillId);
      mutateSkills();
    } catch (e) {
      if (!(e instanceof ApiOfflineError)) setError(String(e.message || e));
    } finally {
      setBusy(false);
    }
  }

  const captureOn = Boolean(config?.capture_enabled);

  return (
    <AppShell loading={isLoading || busy}>
      <div className="cx-label" style={{ marginBottom: 8 }}>
        CAPTURED SKILLS (F6)
      </div>
      <p className="cx-empty-desc" style={{ marginBottom: 16 }}>
        Internal-only behaviour cards from successful gated task chains. Opt-in recording — never leaves the box.
      </p>

      <div className="cx-stats-row" style={{ marginBottom: 20 }}>
        <span>
          Recording:{" "}
          <strong style={{ color: captureOn ? "var(--cx-green)" : "var(--cx-muted)" }}>
            {captureOn ? "ON" : "OFF"}
          </strong>
        </span>
        {canManage && (
          <button type="button" className="cx-entry-btn" onClick={toggleCapture} style={{ marginLeft: 16 }}>
            {captureOn ? "TURN OFF" : "ENABLE CAPTURE"}
          </button>
        )}
      </div>

      {error && <p className="cx-perm-error">{error}</p>}

      {skills?.length ? (
        <table className="cx-data-table">
          <thead>
            <tr>
              <th>INTENT</th>
              <th>TASK</th>
              <th>TRIGGER</th>
              <th>SUPPORT</th>
              <th>SUCCESS</th>
              <th>ACTIVE</th>
              {canManage && <th>ACTION</th>}
            </tr>
          </thead>
          <tbody>
            {skills.map((sk, i) => (
              <tr key={sk.id} className={i % 2 === 1 ? "row-alt" : ""}>
                <td>{sk.intent}</td>
                <td>{sk.task_id}</td>
                <td style={{ maxWidth: 280, overflow: "hidden", textOverflow: "ellipsis" }}>
                  {sk.trigger_pattern}
                </td>
                <td>{sk.support_count}</td>
                <td>{sk.success_count}</td>
                <td>{sk.active ? "yes" : "no"}</td>
                {canManage && (
                  <td>
                    {sk.active && (
                      <button type="button" className="cx-approve-btn" onClick={() => onDeactivate(sk.id)}>
                        DEACTIVATE
                      </button>
                    )}
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        !isLoading && (
          <p className="cx-empty-desc">
            No skills captured yet. Enable recording and complete a gated task with outcome success.
          </p>
        )
      )}
    </AppShell>
  );
}
