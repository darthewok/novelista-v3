import { useEffect, useState } from "react";
import { useOutletContext, useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, Sparkles, Save, ImagePlus, Trash2, BookCheck } from "lucide-react";
import { api, Book, FormatProfile, FrontMatter, KdpKit } from "@/lib/api";
import { useJobStream } from "@/lib/useJobStream";
import { useAction } from "@/lib/useAction";
import { useToast } from "@/lib/toast";
import { GenerationProgress } from "@/components/GenerationProgress";
import { ErrorLine } from "@/components/ErrorLine";

export default function ExportPage() {
  const { id } = useParams();
  const qc = useQueryClient();
  const toast = useToast();
  const { book, format } = useOutletContext<{ book: Book; format?: FormatProfile }>();

  const [acceptedOnly, setAcceptedOnly] = useState(false);
  const [trim, setTrim] = useState(format?.default_trim || "6x9");
  const [coverKey, setCoverKey] = useState(0);

  const { data: fmData } = useQuery({
    queryKey: ["frontmatter", id],
    queryFn: () => api.getFrontMatter(id!),
    enabled: Boolean(id),
  });

  const { data: kitData, refetch: refetchKit } = useQuery({
    queryKey: ["kdp-kit", id],
    queryFn: () => api.getKdpKit(id!),
    enabled: Boolean(id),
  });

  const { data: uploadsData, refetch: refetchUploads } = useQuery({
    queryKey: ["uploads", id],
    queryFn: () => api.listUploads(id!),
    enabled: Boolean(id),
  });
  const hasCover = Boolean(uploadsData?.has_cover);

  const [fm, setFm] = useState<FrontMatter | null>(null);
  const [kit, setKit] = useState<KdpKit | null>(null);

  useEffect(() => {
    if (fmData) setFm(structuredClone(fmData));
  }, [fmData]);

  useEffect(() => {
    if (kitData) setKit(structuredClone(kitData));
  }, [kitData]);

  useEffect(() => {
    if (format?.default_trim) setTrim(format.default_trim);
  }, [format?.default_trim]);

  const stream = useJobStream(async (ev) => {
    if (ev.type === "done") {
      await refetchKit();
      toast.success("Amazon KDP Publishing Kit generated.");
    }
  });

  const saveFmAct = useAction(async () => {
    if (!fm) return;
    await api.putFrontMatter(id!, fm);
    qc.invalidateQueries({ queryKey: ["book", id] });
    toast.success("Front & back matter saved.");
  });

  const saveKitAct = useAction(async () => {
    if (!kit) return;
    await api.putKdpKit(id!, kit);
    toast.success("KDP kit saved.");
  });

  const genKitAct = useAction(async () => {
    const job = await api.genKdpKit(id!);
    stream.start(job.id, { bookId: id!, kind: "kdp_kit" });
  });

  const coverAct = useAction(async (f: File) => {
    await api.uploadCover(id!, f);
    setCoverKey((k) => k + 1);
    await refetchUploads();
    toast.success("Book cover uploaded.");
  });

  const delCoverAct = useAction(async () => {
    await api.deleteCover(id!);
    setCoverKey((k) => k + 1);
    await refetchUploads();
    toast.info("Cover removed.");
  });

  const draftedCount = book.chapters.filter((c) => c.status !== "pending").length;
  const acceptedCount = book.chapters.filter((c) => c.status === "accepted").length;
  const totalWords = book.chapters.reduce((s, c) => s + c.word_count, 0);
  const qs = acceptedOnly ? "accepted_only=true" : "accepted_only=false";

  return (
    <div className="space-y-6">
      {/* Stats & One-Click KDP Downloads */}
      <div className="card p-5 space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-lg font-semibold text-ink flex items-center gap-2">
              <BookCheck className="h-5 w-5 text-accent" /> Publish & Export Studio
            </h2>
            <p className="text-xs text-muted">
              {draftedCount}/{book.chapters.length} {format?.unit_label_plural.toLowerCase() || "chapters"} drafted ({acceptedCount} accepted) · {totalWords.toLocaleString()} total words
            </p>
          </div>
          <label className="flex items-center gap-2 text-xs font-medium cursor-pointer">
            <input type="checkbox" checked={acceptedOnly} onChange={(e) => setAcceptedOnly(e.target.checked)} className="accent-accent" />
            Export Accepted Units Only
          </label>
        </div>

        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
          <a href={`/api/books/${id}/export/epub?${qs}`} className="btn-primary text-xs justify-center py-2.5">
            <Download className="h-3.5 w-3.5" /> EPUB 3 (Kindle / eBook)
          </a>

          <div className="flex gap-1">
            <select
              aria-label="KDP PDF Trim Size"
              value={trim}
              onChange={(e) => setTrim(e.target.value)}
              className="input-field w-24 text-xs py-1.5"
            >
              <option value="5x8">5×8 in</option>
              <option value="5.5x8.5">5.5×8.5</option>
              <option value="6x9">6×9 in</option>
              <option value="8.5x8.5">8.5×8.5</option>
            </select>
            <a href={`/api/books/${id}/export/pdf?trim=${trim}&${qs}`} className="btn-primary text-xs flex-1 justify-center py-2.5">
              <Download className="h-3.5 w-3.5" /> KDP PDF
            </a>
          </div>

          <a href={`/api/books/${id}/export/docx?${qs}`} className="btn-secondary text-xs justify-center py-2.5">
            <Download className="h-3.5 w-3.5" /> DOCX (Word)
          </a>

          <a href={`/api/books/${id}/manuscript?${qs}`} className="btn-secondary text-xs justify-center py-2.5">
            <Download className="h-3.5 w-3.5" /> Markdown
          </a>

          <a href={`/api/books/${id}/export?${qs}`} className="btn-secondary text-xs justify-center py-2.5">
            <Download className="h-3.5 w-3.5" /> Project ZIP
          </a>
        </div>
      </div>

      <ErrorLine error={saveFmAct.error || saveKitAct.error || genKitAct.error || coverAct.error} />
      <GenerationProgress state={stream.state} onCancel={stream.cancel} />

      <div className="grid gap-6 lg:grid-cols-2">
        {/* Front & Back Matter + Cover */}
        <div className="card p-5 space-y-4">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold text-ink">Front & Back Matter + Cover</h3>
            <button type="button" onClick={() => saveFmAct.run()} disabled={saveFmAct.busy || !fm} className="btn-primary text-xs py-1">
              <Save className="h-3.5 w-3.5" /> Save Matter
            </button>
          </div>

          <div className="flex items-center gap-4 rounded-lg border border-border bg-surface-2/50 p-3">
            {hasCover ? (
              <img
                key={coverKey}
                src={`/api/books/${id}/cover?v=${coverKey}`}
                alt="Cover preview"
                className="h-24 w-16 rounded border border-border object-cover shrink-0"
              />
            ) : (
              <div className="h-24 w-16 rounded border border-dashed border-border flex items-center justify-center text-[10px] text-muted text-center px-1 shrink-0">
                No cover
              </div>
            )}
            <div className="flex-1 space-y-1.5 text-xs">
              <div className="font-medium text-ink">Book Cover Image (.jpg / .png / .webp)</div>
              <div className="flex items-center gap-2">
                <label className="btn-secondary py-1 px-2 text-xs cursor-pointer">
                  <ImagePlus className="h-3.5 w-3.5" /> Upload Cover
                  <input
                    type="file"
                    accept=".jpg,.jpeg,.png,.webp"
                    className="hidden"
                    onChange={(e) => {
                      const f = e.target.files?.[0];
                      if (f) coverAct.run(f);
                    }}
                  />
                </label>
                {hasCover && (
                  <button
                    type="button"
                    aria-label="Remove cover"
                    onClick={() => delCoverAct.run()}
                    className="btn-ghost py-1 px-2 text-xs text-muted hover:text-rose-500"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                )}
              </div>
            </div>
          </div>

          {fm && (
            <div className="grid gap-3 sm:grid-cols-2 text-xs">
              <div className="space-y-1">
                <label className="font-medium text-muted">Author Name</label>
                <input
                  className="input-field text-xs"
                  value={fm.author_name}
                  onChange={(e) => setFm({ ...fm, author_name: e.target.value })}
                  placeholder="Pen name or full name"
                />
              </div>
              <div className="space-y-1">
                <label className="font-medium text-muted">Subtitle</label>
                <input
                  className="input-field text-xs"
                  value={fm.subtitle}
                  onChange={(e) => setFm({ ...fm, subtitle: e.target.value })}
                />
              </div>
              <div className="space-y-1">
                <label className="font-medium text-muted">Imprint / Publisher</label>
                <input
                  className="input-field text-xs"
                  value={fm.imprint}
                  onChange={(e) => setFm({ ...fm, imprint: e.target.value })}
                />
              </div>
              <div className="space-y-1">
                <label className="font-medium text-muted">Copyright Year</label>
                <input
                  className="input-field text-xs"
                  value={fm.copyright_year}
                  onChange={(e) => setFm({ ...fm, copyright_year: e.target.value })}
                  placeholder="2026"
                />
              </div>
              <div className="sm:col-span-2 space-y-1">
                <label className="font-medium text-muted">Dedication</label>
                <input
                  className="input-field text-xs"
                  value={fm.dedication}
                  onChange={(e) => setFm({ ...fm, dedication: e.target.value })}
                />
              </div>
              <div className="sm:col-span-2 space-y-1">
                <label className="font-medium text-muted">Acknowledgments</label>
                <textarea
                  rows={2}
                  className="input-field text-xs"
                  value={fm.acknowledgments}
                  onChange={(e) => setFm({ ...fm, acknowledgments: e.target.value })}
                />
              </div>
              <div className="sm:col-span-2 space-y-1">
                <label className="font-medium text-muted">About the Author</label>
                <textarea
                  rows={3}
                  className="input-field text-xs"
                  value={fm.author_bio}
                  onChange={(e) => setFm({ ...fm, author_bio: e.target.value })}
                />
              </div>
            </div>
          )}
        </div>

        {/* Amazon KDP Marketing Kit */}
        <div className="card p-5 space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="text-sm font-semibold text-ink">Amazon KDP Metadata & Marketing Kit</h3>
            <div className="flex items-center gap-2">
              <button type="button" onClick={() => genKitAct.run()} disabled={genKitAct.busy || stream.busy} className="btn-secondary text-xs py-1">
                <Sparkles className="h-3.5 w-3.5 text-accent" /> Generate KDP Kit
              </button>
              <button type="button" onClick={() => saveKitAct.run()} disabled={saveKitAct.busy || !kit} className="btn-primary text-xs py-1">
                <Save className="h-3.5 w-3.5" /> Save Kit
              </button>
            </div>
          </div>

          {kit && (
            <div className="space-y-3 text-xs">
              <div className="space-y-1">
                <label className="font-medium text-muted">KDP Product Blurb (HTML ready)</label>
                <textarea
                  rows={4}
                  className="input-field font-mono text-xs"
                  value={kit.blurb_html}
                  onChange={(e) => setKit({ ...kit, blurb_html: e.target.value })}
                />
              </div>
              <div className="space-y-1">
                <label className="font-medium text-muted">7 Backend Keywords (one per line, &lt;=50 chars each)</label>
                <textarea
                  rows={4}
                  className="input-field font-mono text-xs"
                  value={(kit.keywords || []).join("\n")}
                  onChange={(e) => setKit({ ...kit, keywords: e.target.value.split("\n") })}
                />
              </div>
              <div className="space-y-1">
                <label className="font-medium text-muted">Amazon / BISAC Categories (one per line)</label>
                <textarea
                  rows={2}
                  className="input-field text-xs"
                  value={(kit.categories || []).join("\n")}
                  onChange={(e) => setKit({ ...kit, categories: e.target.value.split("\n") })}
                />
              </div>
              <div className="space-y-1">
                <label className="font-medium text-muted">Cover Art Direction Brief</label>
                <textarea
                  rows={3}
                  className="input-field text-xs"
                  value={kit.cover_art_brief}
                  onChange={(e) => setKit({ ...kit, cover_art_brief: e.target.value })}
                />
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
