import { useEffect, useState } from "react";
import { WorkspaceInfo } from "./api";

interface Props {
  workspaces: WorkspaceInfo[];
  providers: string[];
  running: boolean;
  onSubmit: (workspace: string, goal: string, provider: string) => void;
}

// Left-hand control: pick a workspace + provider, type a goal, trigger a run.
export function RunForm({ workspaces, providers, running, onSubmit }: Props) {
  const [workspace, setWorkspace] = useState("");
  const [provider, setProvider] = useState("");
  const [goal, setGoal] = useState("生成本周项目周报");

  useEffect(() => {
    if (!workspace && workspaces.length) setWorkspace(workspaces[0].name);
  }, [workspaces, workspace]);
  useEffect(() => {
    if (!provider && providers.length) {
      setProvider(providers.includes("stub") ? "stub" : providers[0]);
    }
  }, [providers, provider]);

  return (
    <form
      className="run-form"
      onSubmit={(e) => {
        e.preventDefault();
        if (workspace && goal.trim()) onSubmit(workspace, goal.trim(), provider);
      }}
    >
      <label>
        工作区
        <select value={workspace} onChange={(e) => setWorkspace(e.target.value)}>
          {workspaces.map((w) => (
            <option key={w.name} value={w.name}>
              {w.name} ({w.file_count} 文件)
            </option>
          ))}
        </select>
      </label>
      <label>
        Provider
        <select value={provider} onChange={(e) => setProvider(e.target.value)}>
          {providers.map((p) => (
            <option key={p} value={p}>
              {p}
            </option>
          ))}
        </select>
      </label>
      <label>
        目标
        <textarea
          value={goal}
          rows={3}
          onChange={(e) => setGoal(e.target.value)}
        />
      </label>
      <button type="submit" disabled={running || !workspace}>
        {running ? "运行中…" : "开始运行"}
      </button>
    </form>
  );
}
