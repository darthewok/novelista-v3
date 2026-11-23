import React from "react";

function renderInline(line: string, keyPrefix: string): React.ReactNode[] {
  const nodes: React.ReactNode[] = [];
  const re = /(\*\*[^*\n]+\*\*|\*[^*\n]+\*|`[^`\n]+`)/g;
  let last = 0;
  let idx = 0;
  for (const m of line.matchAll(re)) {
    const start = m.index ?? 0;
    if (start > last) nodes.push(line.slice(last, start));
    const tok = m[0];
    if (tok.startsWith("**")) {
      nodes.push(<strong key={`${keyPrefix}-${idx++}`}>{tok.slice(2, -2)}</strong>);
    } else if (tok.startsWith("*")) {
      nodes.push(<em key={`${keyPrefix}-${idx++}`}>{tok.slice(1, -1)}</em>);
    } else {
      nodes.push(
        <code key={`${keyPrefix}-${idx++}`} className="rounded bg-surface-2 px-1 py-0.5 font-mono text-[0.88em]">
          {tok.slice(1, -1)}
        </code>,
      );
    }
    last = start + tok.length;
  }
  if (last < line.length) nodes.push(line.slice(last));
  return nodes;
}

export function ProseMarkdownView({
  text,
  proseMode = false,
  fontSize = 17,
}: {
  text: string;
  proseMode?: boolean;
  fontSize?: number;
}) {
  const blocks = (text || "").split(/\n\s*\n/).filter((b) => b.trim().length > 0);
  if (!blocks.length) {
    return <p className="text-sm text-muted italic">Nothing written yet.</p>;
  }

  return (
    <div
      className={proseMode ? "font-prose leading-[1.82] mx-auto max-w-[66ch] space-y-4" : "space-y-3 text-sm leading-relaxed"}
      style={proseMode ? { fontSize: `${fontSize}px` } : undefined}
    >
      {blocks.map((rawBlock, i) => {
        const b = rawBlock.trim();
        if (b === "---" || b === "***" || b === "* * *") {
          return (
            <div key={i} className="my-6 text-center tracking-[0.45em] text-muted select-none">
              * * *
            </div>
          );
        }
        if (b.startsWith("### ")) {
          return (
            <h3 key={i} className="text-base font-semibold mt-5 mb-1.5 text-ink">
              {renderInline(b.slice(4), `h3-${i}`)}
            </h3>
          );
        }
        if (b.startsWith("## ")) {
          return (
            <h2
              key={i}
              className={
                proseMode
                  ? "text-2xl font-normal text-center mt-4 mb-6 tracking-tight text-ink"
                  : "text-lg font-semibold mt-5 mb-2 border-b border-border pb-1 text-ink"
              }
            >
              {renderInline(b.slice(3), `h2-${i}`)}
            </h2>
          );
        }
        if (b.startsWith("# ")) {
          return (
            <h1 key={i} className="text-2xl font-bold mt-3 mb-3 text-ink">
              {renderInline(b.slice(2), `h1-${i}`)}
            </h1>
          );
        }
        const lines = b.split("\n");
        if (lines.every((l) => l.trim().startsWith("- ") || l.trim().startsWith("* "))) {
          return (
            <ul key={i} className="list-disc pl-5 space-y-1">
              {lines.map((l, j) => (
                <li key={j}>{renderInline(l.trim().slice(2), `li-${i}-${j}`)}</li>
              ))}
            </ul>
          );
        }
        if (lines.every((l) => l.trim().startsWith("|"))) {
          const rows = lines
            .filter((l) => !/^\|\s*[-:]+/.test(l.trim()))
            .map((l) => l.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim()));
          return (
            <div key={i} className="overflow-x-auto my-3">
              <table className="w-full text-xs border-collapse border border-border">
                <tbody>
                  {rows.map((cells, r) => (
                    <tr key={r} className={r === 0 ? "bg-surface-2 font-semibold" : "border-t border-border"}>
                      {cells.map((c, cidx) => (
                        <td key={cidx} className="px-2.5 py-1.5 border border-border">
                          {renderInline(c, `td-${i}-${r}-${cidx}`)}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          );
        }
        return (
          <p
            key={i}
            className={proseMode && i > 0 ? "indent-6" : ""}
          >
            {renderInline(b.replace(/\n/g, " "), `p-${i}`)}
          </p>
        );
      })}
    </div>
  );
}
