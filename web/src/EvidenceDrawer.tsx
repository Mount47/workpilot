import { useEffect, useState } from "react";
import { api, Evidence } from "./api";

interface Props {
  runId: string;
  evidence: Evidence;
  onClose: () => void;
}

// Slides in from the right when a citation is clicked. Loads the source file
// and highlights exactly the lines the evidence was extracted from — this is
// the payoff of the whole product:结论 → 原文, verifiable in one click.
export function EvidenceDrawer({ runId, evidence, onClose }: Props) {
  const [lines, setLines] = useState<string[] | null>(null);
  const [error, setError] = useState("");

  const sourceId = evidence.locator.source_id;
  const start = evidence.locator.coordinates.start_line ?? 0;
  const end = evidence.locator.coordinates.end_line ?? start;

  useEffect(() => {
    setLines(null);
    setError("");
    api
      .getSource(runId, sourceId)
      .then((s) => setLines(s.lines))
      .catch((e) => setError(String(e)));
  }, [runId, sourceId]);

  // Show a window of context around the cited range.
  const from = Math.max(1, start - 3);
  const to = end + 3;

  return (
    <div className="drawer-overlay" onClick={onClose}>
      <aside className="drawer" onClick={(e) => e.stopPropagation()}>
        <header className="drawer-header">
          <div>
            <div className="drawer-eid">{evidence.evidence_id}</div>
            <div className="drawer-source">
              {sourceId} · L{start}
              {end !== start ? `-L${end}` : ""}
            </div>
          </div>
          <button className="drawer-close" onClick={onClose}>
            ×
          </button>
        </header>
        <blockquote className="drawer-quote">{evidence.quote}</blockquote>
        <div className="drawer-source-body">
          {error && <div className="error">{error}</div>}
          {!lines && !error && <div className="loading">加载原文…</div>}
          {lines &&
            lines.slice(from - 1, to).map((text, i) => {
              const lineNo = from + i;
              const cited = lineNo >= start && lineNo <= end;
              return (
                <div key={lineNo} className={cited ? "src-line cited" : "src-line"}>
                  <span className="ln">{lineNo}</span>
                  <span className="lt">{text || " "}</span>
                </div>
              );
            })}
        </div>
      </aside>
    </div>
  );
}
