// Thin typed client over the WorkPilot HTTP API.

export interface RunStatus {
  run_id: string;
  state: string;
  goal: string;
  workspace: string;
  provider: string;
  failure_reason: string | null;
}

export interface WorkspaceInfo {
  name: string;
  file_count: number;
}

// Shapes below mirror the artifacts the Runtime writes; only the fields the UI
// reads are typed. `Evidence.locator` carries the source coordinates that power
// traceability (jump from a claim to the exact source lines).
export interface EvidenceLocator {
  source_id: string;
  locator_type: string;
  coordinates: { start_line?: number; end_line?: number };
}

export interface Evidence {
  evidence_id: string;
  quote: string;
  evidence_type: string;
  locator: EvidenceLocator;
}

export interface Claim {
  claim_id: string;
  text: string;
  claim_type: string;
  category: string;
  evidence_refs: string[];
}

export interface Snapshot {
  claims: Claim[];
}

export interface VerificationCheck {
  check_id: string;
  status: string;
  severity: string;
  artifact: string;
  location: string;
  message: string;
}

export interface VerificationReport {
  status: string;
  error_count: number;
  total_checks: number;
  checks: VerificationCheck[];
}

export interface SourceFile {
  run_id: string;
  source_id: string;
  lines: string[];
}

const TERMINAL = new Set(["passed", "failed", "cancelled"]);

async function getJSON<T>(url: string): Promise<T> {
  const resp = await fetch(url);
  if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);
  return resp.json() as Promise<T>;
}

export const api = {
  listProviders: () => getJSON<string[]>("/api/providers"),
  listWorkspaces: () => getJSON<WorkspaceInfo[]>("/api/workspaces"),

  createRun: async (workspace: string, goal: string, provider: string) => {
    const resp = await fetch("/api/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ workspace, goal, provider }),
    });
    if (!resp.ok) {
      const detail = await resp.json().catch(() => ({}));
      throw new Error(detail.detail || `${resp.status} ${resp.statusText}`);
    }
    return resp.json() as Promise<RunStatus>;
  },

  getRun: (id: string) => getJSON<RunStatus>(`/api/runs/${id}`),
  getReport: (id: string) =>
    getJSON<{ markdown: string }>(`/api/runs/${id}/report`),
  getSnapshot: (id: string) =>
    getJSON<{ data: Snapshot }>(`/api/runs/${id}/artifacts/snapshot`),
  getEvidence: (id: string) =>
    getJSON<{ data: Evidence[] }>(`/api/runs/${id}/artifacts/evidence`),
  getVerification: (id: string) =>
    getJSON<{ data: VerificationReport }>(
      `/api/runs/${id}/artifacts/verification`,
    ),
  getSource: (id: string, sourceId: string) =>
    getJSON<SourceFile>(`/api/runs/${id}/source/${encodeURIComponent(sourceId)}`),

// Poll status until terminal, invoking onUpdate on each tick.
  pollUntilDone: async (
    id: string,
    onUpdate: (s: RunStatus) => void,
    intervalMs = 600,
  ): Promise<RunStatus> => {
    for (;;) {
      const status = await api.getRun(id);
      onUpdate(status);
      if (TERMINAL.has(status.state)) return status;
      await new Promise((r) => setTimeout(r, intervalMs));
    }
  },
};
