import { useEffect, useState } from "react";
import { api, Evidence, RunStatus, VerificationReport, WorkspaceInfo } from "./api";
import { RunForm } from "./RunForm";
import { ReportView } from "./ReportView";
import { EvidenceDrawer } from "./EvidenceDrawer";
import { VerificationPanel } from "./VerificationPanel";

// Top-level screen: trigger a run on the left, watch it complete, then read
// the traceable report on the right. Clicking an [E-xxxx] marker opens the
// evidence drawer showing the source file with the cited lines highlighted.
export function App() {
  const [workspaces, setWorkspaces] = useState<WorkspaceInfo[]>([]);
  const [providers, setProviders] = useState<string[]>([]);
  const [run, setRun] = useState<RunStatus | null>(null);
  const [report, setReport] = useState<string>("");
  const [evidence, setEvidence] = useState<Record<string, Evidence>>({});
  const [verification, setVerification] = useState<VerificationReport | null>(null);
  const [activeEvidence, setActiveEvidence] = useState<Evidence | null>(null);
  const [error, setError] = useState<string>("");

  useEffect(() => {
    api.listWorkspaces().then(setWorkspaces).catch((e) => setError(String(e)));
    api.listProviders().then(setProviders).catch((e) => setError(String(e)));
  }, []);

  async function startRun(workspace: string, goal: string, provider: string) {
    setError("");
    setReport("");
    setEvidence({});
    setVerification(null);
    setActiveEvidence(null);
    try {
      const started = await api.createRun(workspace, goal, provider);
      setRun(started);
      const final = await api.pollUntilDone(started.run_id, setRun);
      if (final.state === "passed") {
        const [rep, ev, ver] = await Promise.all([
          api.getReport(final.run_id),
          api.getEvidence(final.run_id),
          api.getVerification(final.run_id),
        ]);
        setReport(rep.markdown);
        const byId: Record<string, Evidence> = {};
        for (const e of ev.data) byId[e.evidence_id] = e;
        setEvidence(byId);
        setVerification(ver.data);
      }
    } catch (e) {
      setError(String(e));
    }
  }

  return (
    <div className="app">
      <header className="app-header">
        <h1>WorkPilot</h1>
        <span className="tagline">证据驱动的项目分析 Agent · 每条结论可追溯到原文</span>
      </header>
      <div className="layout">
        <aside className="sidebar">
          <RunForm
            workspaces={workspaces}
            providers={providers}
            onSubmit={startRun}
            running={!!run && !["passed", "failed", "cancelled"].includes(run.state)}
          />
          {run && (
            <div className={`status status-${run.state}`}>
              <div className="status-line">
                <strong>{run.run_id}</strong>
                <span className="state-badge">{run.state}</span>
              </div>
              {run.failure_reason && (
                <div className="failure">{run.failure_reason}</div>
              )}
            </div>
          )}
          {error && <div className="error">{error}</div>}
        </aside>
        <main className="content">
          {report ? (
            <>
              {verification && <VerificationPanel report={verification} />}
              <ReportView
                markdown={report}
                evidence={evidence}
                onCite={setActiveEvidence}
              />
            </>
          ) : (
            <div className="placeholder">
              触发一次运行,生成的周报会显示在这里 —— 点击结论后的
              <code>[E-xxxx]</code> 标记可跳转到原文证据。
            </div>
          )}
        </main>
      </div>
      {activeEvidence && run && (
        <EvidenceDrawer
          runId={run.run_id}
          evidence={activeEvidence}
          onClose={() => setActiveEvidence(null)}
        />
      )}
    </div>
  );
}
