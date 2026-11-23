export interface DiffSegment {
  kind: "same" | "add" | "del";
  text: string;
}

/** Word-level LCS diff preserving whitespace tokens cleanly. */
export function wordDiff(oldText: string, newText: string): DiffSegment[] {
  const a = oldText.split(/(\s+)/);
  const b = newText.split(/(\s+)/);

  // Guard huge chapters (> 4,500 tokens): fall back to line-level LCS so UI stays instantaneous
  if (a.length * b.length > 18_000_000) {
    return lineDiff(oldText, newText);
  }

  const m = a.length;
  const n = b.length;
  const dp: Int32Array[] = Array.from({ length: m + 1 }, () => new Int32Array(n + 1));
  for (let i = m - 1; i >= 0; i--) {
    for (let j = n - 1; j >= 0; j--) {
      dp[i][j] = a[i] === b[j] ? 1 + dp[i + 1][j + 1] : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }

  const raw: DiffSegment[] = [];
  let i = 0;
  let j = 0;
  while (i < m && j < n) {
    if (a[i] === b[j]) {
      raw.push({ kind: "same", text: a[i] });
      i++;
      j++;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      raw.push({ kind: "del", text: a[i] });
      i++;
    } else {
      raw.push({ kind: "add", text: b[j] });
      j++;
    }
  }
  while (i < m) raw.push({ kind: "del", text: a[i++] });
  while (j < n) raw.push({ kind: "add", text: b[j++] });

  return coalesce(raw);
}

function lineDiff(oldText: string, newText: string): DiffSegment[] {
  const a = oldText.split("\n");
  const b = newText.split("\n");
  const m = a.length;
  const n = b.length;
  const dp: Int32Array[] = Array.from({ length: m + 1 }, () => new Int32Array(n + 1));
  for (let i = m - 1; i >= 0; i--) {
    for (let j = n - 1; j >= 0; j--) {
      dp[i][j] = a[i] === b[j] ? 1 + dp[i + 1][j + 1] : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  const raw: DiffSegment[] = [];
  let i = 0;
  let j = 0;
  while (i < m && j < n) {
    if (a[i] === b[j]) {
      raw.push({ kind: "same", text: a[i] + "\n" });
      i++;
      j++;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      raw.push({ kind: "del", text: a[i] + "\n" });
      i++;
    } else {
      raw.push({ kind: "add", text: b[j] + "\n" });
      j++;
    }
  }
  while (i < m) raw.push({ kind: "del", text: a[i++] + "\n" });
  while (j < n) raw.push({ kind: "add", text: b[j++] + "\n" });
  return coalesce(raw);
}

function coalesce(segs: DiffSegment[]): DiffSegment[] {
  const out: DiffSegment[] = [];
  for (const s of segs) {
    if (!s.text) continue;
    const last = out[out.length - 1];
    if (last && last.kind === s.kind) last.text += s.text;
    else out.push({ ...s });
  }
  return out;
}
