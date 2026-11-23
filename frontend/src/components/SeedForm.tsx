import { useState } from "react";
import {
  AUDIENCE_OPTIONS,
  FORMAT_CARDS,
  POV_OPTIONS,
  STYLE_OPTIONS,
  TENSE_OPTIONS,
} from "@/lib/catalog";

export interface SeedFormValue {
  title: string;
  book_format: string;
  audience: string;
  chapter_count: number;
  target_words: number;
  pov: string;
  tense: string;
  writing_style: string;
  custom_style: string;
  genre: string;
  language: string;
  idea: string;
  [key: string]: unknown;
}

export function SeedForm({
  initial,
  submitLabel = "Create Book & Enter Studio",
  busy,
  onSubmit,
}: {
  initial?: Partial<SeedFormValue>;
  submitLabel?: string;
  busy?: boolean;
  onSubmit: (v: SeedFormValue) => void;
}) {
  const [form, setForm] = useState<SeedFormValue>({
    title: initial?.title ?? "",
    book_format: initial?.book_format ?? "novel",
    audience: initial?.audience ?? "adult",
    chapter_count: initial?.chapter_count ?? 20,
    target_words: initial?.target_words ?? 65000,
    pov: initial?.pov ?? "third_limited",
    tense: initial?.tense ?? "past",
    writing_style: initial?.writing_style ?? "literary_contemporary",
    custom_style: initial?.custom_style ?? "",
    genre: initial?.genre ?? "",
    language: initial?.language ?? "English",
    idea: initial?.idea ?? "",
  });

  const activeFmt = FORMAT_CARDS.find((f) => f.value === form.book_format) || FORMAT_CARDS[5];
  const perUnit = form.chapter_count > 0 ? Math.round(form.target_words / form.chapter_count) : form.target_words;

  const pickFormat = (fmtKey: string) => {
    const fc = FORMAT_CARDS.find((f) => f.value === fmtKey);
    if (!fc) return;
    setForm((s) => ({
      ...s,
      book_format: fmtKey,
      audience: fc.defaultAudience,
      chapter_count: fc.defaultUnits,
      target_words: fc.defaultWords,
      writing_style:
        fmtKey === "picture_book" || fmtKey === "early_reader"
          ? "storybook_warm"
          : fmtKey === "chapter_book"
            ? "middle_grade_adventure"
            : s.writing_style,
    }));
  };

  const styleGroups = Array.from(new Set(STYLE_OPTIONS.map((s) => s.group || "Other")));

  return (
    <form
      className="space-y-6"
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit({ ...form, title: form.title.trim() || "Untitled" });
      }}
    >
      {/* Format Cards */}
      <div className="space-y-2">
        <label className="text-xs font-semibold uppercase tracking-wider text-muted">1. Book Format</label>
        <div className="grid gap-2.5 sm:grid-cols-2 lg:grid-cols-3">
          {FORMAT_CARDS.map((fc) => {
            const active = form.book_format === fc.value;
            return (
              <button
                key={fc.value}
                type="button"
                aria-pressed={active}
                onClick={() => pickFormat(fc.value)}
                className={`text-left rounded-xl border p-3 transition ${
                  active ? "border-accent bg-accent-soft ring-1 ring-accent" : "border-border bg-surface hover:border-accent/40"
                }`}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="font-semibold text-sm text-ink">{fc.label}</span>
                </div>
                <div className="text-[11px] font-medium text-accent mt-0.5">{fc.badge}</div>
                <p className="text-xs text-muted mt-1 leading-snug">{fc.desc}</p>
              </button>
            );
          })}
        </div>
      </div>

      {/* Title, Genre, Language */}
      <div className="grid gap-4 sm:grid-cols-3">
        <div className="sm:col-span-2 space-y-1">
          <label htmlFor="book-title" className="text-xs font-semibold text-muted">Working Title</label>
          <input
            id="book-title"
            className="input-field"
            placeholder="e.g., The Lantern-Maker's Ledger (or leave blank)"
            value={form.title}
            onChange={(e) => setForm((s) => ({ ...s, title: e.target.value }))}
          />
        </div>
        <div className="space-y-1">
          <label htmlFor="book-genre" className="text-xs font-semibold text-muted">Genre / Shelf</label>
          <input
            id="book-genre"
            className="input-field"
            placeholder="e.g., Historical Mystery, Bedtime Tale"
            value={form.genre}
            onChange={(e) => setForm((s) => ({ ...s, genre: e.target.value }))}
          />
        </div>
      </div>

      {/* Audience, Units, Target Words, Language */}
      <div className="grid gap-4 sm:grid-cols-4">
        <div className="space-y-1">
          <label htmlFor="book-audience" className="text-xs font-semibold text-muted">Audience</label>
          <select
            id="book-audience"
            className="input-field"
            value={form.audience}
            onChange={(e) => setForm((s) => ({ ...s, audience: e.target.value }))}
          >
            {AUDIENCE_OPTIONS.map((a) => (
              <option key={a.value} value={a.value}>
                {a.label}
              </option>
            ))}
          </select>
        </div>
        <div className="space-y-1">
          <label htmlFor="book-units" className="text-xs font-semibold text-muted">{activeFmt.unitLabel} Count</label>
          <input
            id="book-units"
            type="number"
            min={1}
            max={200}
            className="input-field"
            value={form.chapter_count}
            onChange={(e) => setForm((s) => ({ ...s, chapter_count: Math.max(1, Number(e.target.value) || 1) }))}
          />
        </div>
        <div className="space-y-1">
          <label htmlFor="book-words" className="text-xs font-semibold text-muted">
            Target Words <span className="text-accent font-normal">(~{perUnit.toLocaleString()}/{activeFmt.unitLabel.toLowerCase()})</span>
          </label>
          <input
            id="book-words"
            type="number"
            min={150}
            max={2000000}
            step={50}
            className="input-field"
            value={form.target_words}
            onChange={(e) => setForm((s) => ({ ...s, target_words: Math.max(150, Number(e.target.value) || 150) }))}
          />
        </div>
        <div className="space-y-1">
          <label htmlFor="book-lang" className="text-xs font-semibold text-muted">Language</label>
          <input
            id="book-lang"
            className="input-field"
            value={form.language}
            onChange={(e) => setForm((s) => ({ ...s, language: e.target.value }))}
          />
        </div>
      </div>

      {/* POV & Tense */}
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-1.5">
          <span className="text-xs font-semibold text-muted">Point of View</span>
          <div className="grid grid-cols-2 gap-2">
            {POV_OPTIONS.map((p) => {
              const active = form.pov === p.value;
              return (
                <button
                  key={p.value}
                  type="button"
                  aria-pressed={active}
                  onClick={() => setForm((s) => ({ ...s, pov: p.value }))}
                  className={`rounded-lg border p-2 text-left text-xs transition ${
                    active ? "border-accent bg-accent-soft text-ink font-medium" : "border-border bg-surface text-muted hover:text-ink"
                  }`}
                >
                  <div className="font-semibold text-ink">{p.label}</div>
                  <div className="text-[11px] opacity-80">{p.desc}</div>
                </button>
              );
            })}
          </div>
        </div>

        <div className="space-y-1.5">
          <span className="text-xs font-semibold text-muted">Narrative Tense</span>
          <div className="grid grid-cols-2 gap-2">
            {TENSE_OPTIONS.map((t) => {
              const active = form.tense === t.value;
              return (
                <button
                  key={t.value}
                  type="button"
                  aria-pressed={active}
                  onClick={() => setForm((s) => ({ ...s, tense: t.value }))}
                  className={`rounded-lg border p-2 text-left text-xs transition ${
                    active ? "border-accent bg-accent-soft text-ink font-medium" : "border-border bg-surface text-muted hover:text-ink"
                  }`}
                >
                  <div className="font-semibold text-ink">{t.label}</div>
                  <div className="text-[11px] opacity-80">{t.desc}</div>
                </button>
              );
            })}
          </div>
        </div>
      </div>

      {/* Writing Style */}
      <div className="space-y-2">
        <label htmlFor="book-style" className="text-xs font-semibold uppercase tracking-wider text-muted">Prose Voice & Style</label>
        <div className="grid gap-3 sm:grid-cols-2">
          <select
            id="book-style"
            className="input-field"
            value={form.writing_style}
            onChange={(e) => setForm((s) => ({ ...s, writing_style: e.target.value }))}
          >
            {styleGroups.map((g) => (
              <optgroup key={g} label={g}>
                {STYLE_OPTIONS.filter((s) => (s.group || "Other") === g).map((st) => (
                  <option key={st.value} value={st.value}>
                    {st.label} — {st.desc}
                  </option>
                ))}
              </optgroup>
            ))}
          </select>
          <input
            className="input-field"
            placeholder="Optional custom voice notes (e.g., dry maritime wit, cozy tea-shop rhythm)"
            value={form.custom_style}
            onChange={(e) => setForm((s) => ({ ...s, custom_style: e.target.value }))}
          />
        </div>
      </div>

      {/* Seed Idea */}
      <div className="space-y-1.5">
        <label htmlFor="book-idea" className="text-xs font-semibold uppercase tracking-wider text-muted">
          Story Premise / Seed Idea <span className="font-normal normal-case">(optional — you can brainstorm 10 ideas inside the Seed Studio)</span>
        </label>
        <textarea
          id="book-idea"
          rows={4}
          className="prose-editor text-sm leading-relaxed"
          placeholder="What is this book about? Characters, setting, central trouble, or themes..."
          value={form.idea}
          onChange={(e) => setForm((s) => ({ ...s, idea: e.target.value }))}
        />
      </div>

      <div className="flex justify-end">
        <button type="submit" disabled={busy} className="btn-primary px-5 py-2.5">
          {busy ? "Creating…" : submitLabel}
        </button>
      </div>
    </form>
  );
}
