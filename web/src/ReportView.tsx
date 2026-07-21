import { Fragment } from "react";
import { Evidence } from "./api";

interface Props {
  markdown: string;
  evidence: Record<string, Evidence>;
  onCite: (evidence: Evidence) => void;
}

const EVIDENCE_RE = /\[(E-\d+)\]/g;

// Render the weekly report. This intentionally does NOT pull in a full
// markdown library — the report is a flat list of `##` headings and `-`
// bullets, so a light line renderer keeps the bundle tiny and, more
// importantly, lets us turn each [E-xxxx] marker into a clickable citation.
export function ReportView({ markdown, evidence, onCite }: Props) {
  const lines = markdown.split("\n");
  return (
    <article className="report">
      {lines.map((line, i) => {
        if (line.startsWith("## ")) {
          return <h2 key={i}>{line.slice(3)}</h2>;
        }
        if (line.startsWith("- ")) {
          return (
            <p key={i} className="bullet">
              {renderInline(line.slice(2), evidence, onCite)}
            </p>
          );
        }
        if (!line.trim()) return null;
        return (
          <p key={i}>{renderInline(line, evidence, onCite)}</p>
        );
      })}
    </article>
  );
}

// Split a line on evidence markers and render each marker as a citation chip.
function renderInline(
  text: string,
  evidence: Record<string, Evidence>,
  onCite: (evidence: Evidence) => void,
) {
  const parts: (string | { id: string })[] = [];
  let last = 0;
  for (const m of text.matchAll(EVIDENCE_RE)) {
    const idx = m.index ?? 0;
    if (idx > last) parts.push(text.slice(last, idx));
    parts.push({ id: m[1] });
    last = idx + m[0].length;
  }
  if (last < text.length) parts.push(text.slice(last));

  return parts.map((part, i) => {
    if (typeof part === "string") return <Fragment key={i}>{part}</Fragment>;
    const ev = evidence[part.id];
    return (
      <button
        key={i}
        className={ev ? "cite" : "cite cite-missing"}
        title={ev ? ev.quote : "证据缺失"}
        disabled={!ev}
        onClick={() => ev && onCite(ev)}
      >
        {part.id}
      </button>
    );
  });
}
