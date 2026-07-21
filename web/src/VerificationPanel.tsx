import { useState } from "react";
import { VerificationReport } from "./api";

interface Props {
  report: VerificationReport;
}

// The evidence-gating summary: this is what makes WorkPilot's output
// trustworthy — every claim passed a deterministic support contract before
// the report was released. Failures and warnings surface first; the full
// passing set is collapsed by default to keep the signal clean.
export function VerificationPanel({ report }: Props) {
  const [expanded, setExpanded] = useState(false);
  const passed = report.status === "passed";
  const problems = report.checks.filter(
    (c) => c.status !== "passed" || c.severity === "error" || c.severity === "warning",
  );
  const passedCount = report.total_checks - report.error_count;

  return (
    <section className={`verify ${passed ? "verify-ok" : "verify-bad"}`}>
      <div className="verify-summary">
        <span className="verify-icon">{passed ? "✓" : "✕"}</span>
        <div>
          <div className="verify-title">
            {passed ? "证据门控通过" : "证据门控未通过"}
          </div>
          <div className="verify-sub">
            {passedCount}/{report.total_checks} 项校验通过
            {report.error_count > 0 && ` · ${report.error_count} 个错误`}
          </div>
        </div>
      </div>

      {problems.length > 0 && (
        <ul className="verify-list">
          {problems.map((c) => (
            <li key={c.check_id + c.location} className={`vc vc-${c.severity}`}>
              <span className="vc-sev">{c.severity}</span>
              <span className="vc-loc">{c.location}</span>
              <span className="vc-msg">{c.message}</span>
            </li>
          ))}
        </ul>
      )}

      <button className="verify-toggle" onClick={() => setExpanded((v) => !v)}>
        {expanded ? "收起全部校验" : `查看全部 ${report.total_checks} 项校验`}
      </button>
      {expanded && (
        <ul className="verify-list verify-all">
          {report.checks.map((c) => (
            <li key={c.check_id + c.location} className={`vc vc-${c.status}`}>
              <span className="vc-sev">{c.status === "passed" ? "✓" : c.severity}</span>
              <span className="vc-loc">{c.location}</span>
              <span className="vc-msg">{c.message}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
