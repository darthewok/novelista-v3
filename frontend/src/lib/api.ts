export interface FrontMatter {
  subtitle: string;
  author_name: string;
  series_name: string;
  series_number: string;
  copyright_year: string;
  imprint: string;
  dedication: string;
  epigraph: string;
  acknowledgments: string;
  author_bio: string;
  also_by: string;
  blurb: string;
}

export interface ChapterSummary {
  number: number;
  title: string;
  status: "pending" | "drafted" | "revising" | "polished" | "accepted";
  word_count: number;
  version: number;
  tells: number;
  error_count: number;
  warning_count: number;
  cleanliness_grade: string;
  pov: string;
  scene_count: number;
  has_illustration_prompt: boolean;
}

export interface ChapterDetail extends ChapterSummary {
  text: string;
}

export interface Book {
  id: string;
  title: string;
  status: "idea" | "planning" | "drafting" | "polishing" | "ready";
  book_format: string;
  audience: string;
  chapter_count: number | null;
  target_words: number | null;
  pov: string;
  tense: string;
  writing_style: string;
  custom_style: string;
  genre: string;
  language: string;
  idea: string;
  front_matter: FrontMatter;
  created_at: string;
  updated_at: string;
  chapters: ChapterSummary[];
}

export interface FormatProfile {
  key: string;
  label: string;
  unit_label: string;
  unit_label_plural: string;
  default_units: number;
  min_units: number;
  max_units: number;
  default_words: number;
  min_words: number;
  max_words: number;
  default_audience: string;
  optional_phases: string[];
  illustration_support: boolean;
  default_trim: string;
}

export interface Finding {
  rule: string;
  severity: "error" | "warning";
  line: number;
  quote: string;
  message: string;
  start_char: number;
  end_char: number;
  full_sentence: string;
  in_dialogue: boolean;
  fingerprint: string;
}

export interface Report {
  error_count: number;
  warning_count: number;
  tells_total: number;
  cleanliness_grade: string;
  metrics: {
    word_count?: number;
    bytes_per_word?: number;
    paragraph_count?: number;
    paragraph_count_unique?: number;
    unique_paragraph_ratio?: number;
    sentence_count?: number;
    sentence_len_cv?: number;
    paragraph_len_cv?: number;
    em_dash_count?: number;
    em_dash_per_150?: number;
    felt_per_1000?: number;
    dialogue_pct?: number;
    flesch_kincaid_grade?: number;
    tells_per_1000?: number;
  };
  findings: Finding[];
}

export interface SceneCard {
  title: string;
  word_budget: number;
  location: string;
  time: string;
  pov: string;
  cast: string[];
  start_state: string;
  goal: string;
  turn: string;
  outcome: string;
  hard_stop: string;
  illustration_prompt: string;
}

export interface JobOut {
  id: string;
  book_id: string;
  kind: string;
  status: "queued" | "running" | "paused" | "done" | "error" | "cancelled";
  created_at: string;
  finished_at: string | null;
  error: string | null;
  chapter: number | null;
  batch_id: string | null;
}

export interface ProviderCfg {
  api_key: string;
  has_key?: boolean;
  base_url: string;
  num_ctx: number;
  repeat_penalty: number;
  repeat_last_n: number;
  top_p: number;
  min_p: number;
  frequency_penalty: number;
  timeout_sec: number;
}

export interface SettingsData {
  provider: string;
  providers: Record<string, ProviderCfg>;
  models: Record<string, string>;
  quality: {
    auto_polish: boolean;
    max_polish_passes: number;
    tells_threshold: number;
    auto_scene_cards: boolean;
    auto_continue_truncated: boolean;
  };
}

export interface ConnectionTestResult {
  provider: string;
  url: string;
  ok: boolean;
  models_present: boolean;
  available_models: string[];
  model_meta: Record<string, { parameter_size?: string; parameter_billions?: number; family?: string; quantization?: string }>;
  warnings: string[];
  error: string | null;
}

export interface KdpKit {
  subtitle: string;
  blurb_html: string;
  keywords: string[];
  categories: string[];
  comp_titles: string[];
  age_range: string;
  cover_art_brief: string;
}

async function req<T>(url: string, init?: RequestInit & { timeoutMs?: number }): Promise<T> {
  const signal = init?.signal ?? (init?.timeoutMs ? AbortSignal.timeout(init.timeoutMs) : AbortSignal.timeout(30_000));
  const res = await fetch(url, {
    ...init,
    signal,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers || {}),
    },
  });
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      const data = await res.json();
      if (data?.detail) msg = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
    } catch {
      /* ignore */
    }
    throw new Error(msg);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

async function uploadFile<T>(url: string, file: File): Promise<T> {
  const fd = new FormData();
  fd.append("file", file);
  const res = await fetch(url, { method: "POST", body: fd, signal: AbortSignal.timeout(60_000) });
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      const data = await res.json();
      if (data?.detail) msg = String(data.detail);
    } catch {
      /* ignore */
    }
    throw new Error(msg);
  }
  return res.json() as Promise<T>;
}

export const api = {
  // Books & Formats
  getFormats: () => req<{ formats: FormatProfile[] }>("/api/books/formats"),
  listBooks: () => req<Book[]>("/api/books"),
  createBook: (body: Partial<Book> & Record<string, unknown>) =>
    req<Book>("/api/books", { method: "POST", body: JSON.stringify(body) }),
  getBook: (id: string) => req<Book>(`/api/books/${id}`),
  patchBook: (id: string, body: Record<string, unknown>) =>
    req<Book>(`/api/books/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteBook: (id: string) => req<void>(`/api/books/${id}`, { method: "DELETE" }),
  getFrontMatter: (id: string) => req<FrontMatter>(`/api/books/${id}/frontmatter`),
  putFrontMatter: (id: string, fm: FrontMatter) =>
    req<FrontMatter>(`/api/books/${id}/frontmatter`, { method: "PUT", body: JSON.stringify(fm) }),
  searchBook: (id: string, q: string) =>
    req<{ query: string; count: number; hits: Array<{ label: string; target: string; chapter: number | null; line: number; snippet: string }> }>(
      `/api/books/${id}/search?q=${encodeURIComponent(q)}`,
    ),

  // Seed
  getSeed: (id: string) => req<{ text: string }>(`/api/books/${id}/seed`),
  putSeed: (id: string, text: string) =>
    req<{ text: string }>(`/api/books/${id}/seed`, { method: "PUT", body: JSON.stringify({ text }) }),
  getSeedCandidates: (id: string) =>
    req<{ text: string; candidates: Array<{ index: number; title: string; text: string }> }>(`/api/books/${id}/seed/candidates`),
  genSeedCandidates: (id: string) => req<JobOut>(`/api/books/${id}/seed/candidates`, { method: "POST" }),
  chooseSeed: (id: string, body: { text?: string; index?: number }) =>
    req<{ text: string }>(`/api/books/${id}/seed/choose`, { method: "POST", body: JSON.stringify(body) }),

  // Uploads, Cover & Illustrations
  listUploads: (id: string) =>
    req<{ files: Array<{ name: string; size: number }>; has_extracted: boolean; has_cover?: boolean }>(`/api/books/${id}/uploads`),
  uploadRef: (id: string, file: File) => uploadFile<{ name: string; size: number; extracted_chars: number }>(`/api/books/${id}/uploads`, file),
  deleteUpload: (id: string, name: string) => req<void>(`/api/books/${id}/uploads/${encodeURIComponent(name)}`, { method: "DELETE" }),
  uploadCover: (id: string, file: File) => uploadFile<{ name: string; size: number }>(`/api/books/${id}/cover`, file),
  deleteCover: (id: string) => req<void>(`/api/books/${id}/cover`, { method: "DELETE" }),
  uploadIllustration: (id: string, n: number, file: File) =>
    uploadFile<{ name: string; size: number }>(`/api/books/${id}/chapters/${n}/illustration`, file),
  deleteIllustration: (id: string, n: number) =>
    req<void>(`/api/books/${id}/chapters/${n}/illustration`, { method: "DELETE" }),

  // Foundation artifacts
  getArtifact: (id: string, kind: string) => req<{ text: string; has_prev: boolean }>(`/api/books/${id}/${kind}`),
  putArtifact: (id: string, kind: string, text: string) =>
    req<{ text: string; has_prev: boolean }>(`/api/books/${id}/${kind}`, { method: "PUT", body: JSON.stringify({ text }) }),
  restoreArtifactPrev: (id: string, kind: string) =>
    req<{ text: string; has_prev: boolean }>(`/api/books/${id}/${kind}/restore-prev`, { method: "POST" }),
  genArtifact: (id: string, kind: string, opts?: { choice?: number }) =>
    req<JobOut>(`/api/books/${id}/${kind}/generate`, { method: "POST", body: JSON.stringify(opts || {}) }),
  getVoiceTrials: (id: string) =>
    req<{ text: string; trials: Array<{ index: number; register: string; text: string }> }>(`/api/books/${id}/voice/trials`),
  genVoiceTrials: (id: string) => req<JobOut>(`/api/books/${id}/voice/trials`, { method: "POST" }),

  // Outline
  getOutline: (id: string) => req<{ text: string; has_prev: boolean; chapters: ChapterSummary[] }>(`/api/books/${id}/outline`),
  putOutline: (id: string, text: string) =>
    req<{ text: string; chapter_count: number; chapters: ChapterSummary[] }>(`/api/books/${id}/outline`, {
      method: "PUT",
      body: JSON.stringify({ text }),
    }),
  genOutline: (id: string) => req<JobOut>(`/api/books/${id}/outline/generate`, { method: "POST" }),

  // Chapters
  listChapters: (id: string) => req<ChapterSummary[]>(`/api/books/${id}/chapters`),
  getChapter: (id: string, n: number) => req<ChapterDetail>(`/api/books/${id}/chapters/${n}`),
  putChapter: (id: string, n: number, text: string) =>
    req<{ word_count: number; status: string; report: Report }>(`/api/books/${id}/chapters/${n}`, {
      method: "PUT",
      body: JSON.stringify({ text }),
    }),
  scanChapter: (id: string, n: number) => req<Report>(`/api/books/${id}/chapters/${n}/scan`, { method: "POST" }),
  getReport: (id: string, n: number) => req<Report>(`/api/books/${id}/chapters/${n}/report`),
  getBrief: (id: string, n: number) => req<{ text: string }>(`/api/books/${id}/chapters/${n}/brief`),
  putBrief: (id: string, n: number, text: string) =>
    req<{ text: string }>(`/api/books/${id}/chapters/${n}/brief`, { method: "PUT", body: JSON.stringify({ text }) }),
  listVersions: (id: string, n: number) => req<{ current_version: number; versions: number[] }>(`/api/books/${id}/chapters/${n}/versions`),
  getVersion: (id: string, n: number, v: number) => req<{ version: number; text: string }>(`/api/books/${id}/chapters/${n}/versions/${v}`),
  restoreVersion: (id: string, n: number, v: number) =>
    req<{ text: string; report: Report }>(`/api/books/${id}/chapters/${n}/versions/${v}/restore`, { method: "POST" }),

  getScenes: (id: string, n: number) => req<{ chapter: number; scenes: SceneCard[] }>(`/api/books/${id}/chapters/${n}/scenes`),
  putScenes: (id: string, n: number, scenes: SceneCard[]) =>
    req<{ chapter: number; scenes: SceneCard[] }>(`/api/books/${id}/chapters/${n}/scenes`, {
      method: "PUT",
      body: JSON.stringify({ chapter: n, scenes }),
    }),
  genScenes: (id: string, n: number) => req<JobOut>(`/api/books/${id}/chapters/${n}/scenes/generate`, { method: "POST" }),

  acceptChapter: (id: string, n: number) => req<ChapterSummary>(`/api/books/${id}/chapters/${n}/accept`, { method: "POST" }),
  unacceptChapter: (id: string, n: number) => req<ChapterSummary>(`/api/books/${id}/chapters/${n}/unaccept`, { method: "POST" }),
  draftChapter: (id: string, n: number, opts?: { polish?: boolean; scene_cards?: boolean }) =>
    req<JobOut>(`/api/books/${id}/chapters/${n}/draft`, { method: "POST", body: JSON.stringify(opts || {}) }),
  reviseChapter: (id: string, n: number, body: { notes: string; polish?: boolean }) =>
    req<JobOut>(`/api/books/${id}/chapters/${n}/revise`, { method: "POST", body: JSON.stringify(body) }),
  polishChapter: (id: string, n: number) => req<JobOut>(`/api/books/${id}/chapters/${n}/polish`, { method: "POST" }),
  editorChapter: (id: string, n: number) => req<JobOut>(`/api/books/${id}/chapters/${n}/editor`, { method: "POST" }),
  tightenChapter: (id: string, n: number) => req<JobOut>(`/api/books/${id}/chapters/${n}/tighten`, { method: "POST" }),
  fixTells: (id: string, n: number) => req<JobOut>(`/api/books/${id}/chapters/${n}/fix-tells`, { method: "POST" }),
  fixFinding: (id: string, n: number, body: { finding_id: string; start_char?: number; end_char?: number }) =>
    req<{ text: string; report: Report }>(`/api/books/${id}/chapters/${n}/fix-finding`, {
      method: "POST",
      body: JSON.stringify(body),
      timeoutMs: 90_000,
    }),
  ignoreFinding: (id: string, n: number, fingerprint: string) =>
    req<Report>(`/api/books/${id}/chapters/${n}/ignore-finding`, { method: "POST", body: JSON.stringify({ fingerprint }) }),
  rewriteSelection: (
    id: string,
    n: number,
    body: { start_char: number; end_char: number; action: string; instruction?: string },
  ) =>
    req<{ start_char: number; end_char: number; original: string; replacement: string }>(
      `/api/books/${id}/chapters/${n}/rewrite-selection`,
      { method: "POST", body: JSON.stringify(body), timeoutMs: 90_000 },
    ),

  draftAll: (id: string, opts?: { polish?: boolean; scene_cards?: boolean }) =>
    req<{ batch_id: string; chapters: number[]; job_ids: string[] }>(`/api/books/${id}/draft-all`, {
      method: "POST",
      body: JSON.stringify(opts || {}),
    }),
  pauseBatch: (id: string, batchId: string) =>
    req<{ paused: boolean }>(`/api/books/${id}/draft-all/${batchId}/pause`, { method: "POST" }),
  cancelBatch: (id: string, batchId: string) =>
    req<{ cancelled: number }>(`/api/books/${id}/draft-all/${batchId}/cancel`, { method: "POST" }),

  // Audit & KDP Kit
  getAudit: (id: string) => req<{ deterministic: any; editorial: any }>(`/api/books/${id}/audit`),
  runAudit: (id: string) => req<JobOut>(`/api/books/${id}/audit/run`, { method: "POST" }),
  fixAuditIssues: (
    id: string,
    body: {
      global_issues?: string[];
      priority_fixes?: Array<{ chapter: number; action: string }>;
      fix_all?: boolean;
      rerun_audit?: boolean;
    },
  ) => req<JobOut>(`/api/books/${id}/audit/fix`, { method: "POST", body: JSON.stringify(body) }),
  getKdpKit: (id: string) => req<KdpKit>(`/api/books/${id}/kdp-kit`),
  putKdpKit: (id: string, kit: KdpKit) => req<KdpKit>(`/api/books/${id}/kdp-kit`, { method: "PUT", body: JSON.stringify(kit) }),
  genKdpKit: (id: string) => req<JobOut>(`/api/books/${id}/kdp-kit/generate`, { method: "POST" }),

  // Settings & Jobs
  getSettings: () => req<SettingsData>("/api/settings"),
  putSettings: (patch: Record<string, unknown>) =>
    req<SettingsData>("/api/settings", { method: "PUT", body: JSON.stringify(patch) }),
  getSettingsPresets: () => req<{ task_roles: string[]; defaults_by_provider: Record<string, Record<string, string>> }>("/api/settings/presets"),
  testSettings: () => req<ConnectionTestResult>("/api/settings/test", { method: "POST", timeoutMs: 25_000 }),
  getActiveJobs: (bookId?: string) =>
    req<JobOut[]>(`/api/jobs/active${bookId ? `?book_id=${encodeURIComponent(bookId)}` : ""}`),
  getJob: (jobId: string) => req<JobOut>(`/api/jobs/${jobId}`),
  cancelJob: (jobId: string) => req<{ cancelled: boolean }>(`/api/jobs/${jobId}/cancel`, { method: "POST" }),
};
