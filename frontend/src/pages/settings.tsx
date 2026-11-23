import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, AlertTriangle, RefreshCw, Save, Trash2, Wand2 } from "lucide-react";
import { api, SettingsData } from "@/lib/api";
import { PROVIDER_META, TASK_LABELS } from "@/lib/catalog";
import { useAction } from "@/lib/useAction";
import { useToast } from "@/lib/toast";
import { ErrorLine } from "@/components/ErrorLine";

export default function SettingsPage() {
  const qc = useQueryClient();
  const toast = useToast();

  const { data: settings, isLoading, error: loadErr } = useQuery({
    queryKey: ["settings"],
    queryFn: api.getSettings,
  });

  const { data: presets } = useQuery({
    queryKey: ["settings-presets"],
    queryFn: api.getSettingsPresets,
    staleTime: Infinity,
  });

  const {
    data: testRes,
    refetch: runTest,
    isFetching: testing,
  } = useQuery({
    queryKey: ["connection-test"],
    queryFn: api.testSettings,
    staleTime: 60_000,
    retry: false,
  });

  const [form, setForm] = useState<SettingsData | null>(null);
  const [newKeyInput, setNewKeyInput] = useState("");
  const [bulkModel, setBulkModel] = useState("");

  useEffect(() => {
    if (settings) {
      setForm(structuredClone(settings));
      setNewKeyInput("");
    }
  }, [settings]);

  const saveAct = useAction(async (overrideForm?: SettingsData) => {
    const f = overrideForm || form;
    if (!f) return;
    const pKey = f.provider;
    const curProv = f.providers[pKey];
    const providerPatch: Record<string, unknown> = {
      base_url: curProv?.base_url ?? "",
      num_ctx: curProv?.num_ctx ?? 32768,
      repeat_penalty: curProv?.repeat_penalty ?? 1.12,
      repeat_last_n: curProv?.repeat_last_n ?? 256,
      top_p: curProv?.top_p ?? 0.9,
      min_p: curProv?.min_p ?? 0.05,
      frequency_penalty: curProv?.frequency_penalty ?? 0.0,
      timeout_sec: curProv?.timeout_sec ?? 180,
    };
    if (newKeyInput.trim()) {
      providerPatch.api_key = newKeyInput.trim();
    }

    const updated = await api.putSettings({
      provider: pKey,
      providers: { [pKey]: providerPatch },
      models: f.models,
      quality: f.quality,
    });
    setForm(structuredClone(updated));
    setNewKeyInput("");
    qc.invalidateQueries({ queryKey: ["settings"] });
    await runTest();
    toast.success("Settings saved and connection verified.");
  });

  const clearKeyAct = useAction(async () => {
    if (!form) return;
    const updated = await api.putSettings({
      providers: { [form.provider]: { api_key: null } },
    });
    setForm(structuredClone(updated));
    setNewKeyInput("");
    qc.invalidateQueries({ queryKey: ["settings"] });
    await runTest();
    toast.info(`Cleared API key for ${form.provider}.`);
  });

  if (isLoading) return <p className="text-sm text-muted py-8">Loading settings…</p>;
  if (loadErr) return <ErrorLine error={loadErr instanceof Error ? loadErr.message : String(loadErr)} />;
  if (!form) return null;

  const provMeta = PROVIDER_META.find((p) => p.key === form.provider) || PROVIDER_META[0];
  const provCfg = form.providers[form.provider];
  const availableModels = testRes?.provider === form.provider ? testRes.available_models : [];
  const modelMeta = testRes?.provider === form.provider ? testRes.model_meta || {} : {};

  const applyAllModels = (m: string) => {
    if (!m.trim()) return;
    const nextModels: Record<string, string> = {};
    for (const k of Object.keys(TASK_LABELS)) nextModels[k] = m.trim();
    setForm((s) => (s ? { ...s, models: nextModels } : s));
    toast.info(`Assigned "${m}" to all task roles (click Save to apply).`);
  };

  const applyProviderDefaults = () => {
    const defs = presets?.defaults_by_provider?.[form.provider];
    if (!defs) return;
    setForm((s) => (s ? { ...s, models: { ...s.models, ...defs } } : s));
    toast.info(`Applied recommended ${provMeta.label} model preset.`);
  };

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="font-prose text-2xl font-bold text-ink">Model & Quality Settings</h1>
          <p className="text-sm text-muted">
            BYOK keys stay locally in <code className="text-xs">data/settings.json</code> and never leave your machine except to the chosen provider.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button type="button" onClick={() => runTest()} disabled={testing} className="btn-secondary">
            <RefreshCw className={`h-4 w-4 ${testing ? "animate-spin" : ""}`} /> Test Connection
          </button>
          <button type="button" onClick={() => saveAct.run()} disabled={saveAct.busy} className="btn-primary">
            <Save className="h-4 w-4" /> Save Settings
          </button>
        </div>
      </div>

      <ErrorLine error={saveAct.error || clearKeyAct.error} />

      {/* Connection Status Card */}
      {testRes && (
        <div
          className={`card p-3.5 text-sm space-y-1.5 ${
            testRes.ok ? "border-emerald-500/40 bg-emerald-500/5" : "border-rose-500/40 bg-rose-500/5"
          }`}
        >
          <div className="flex items-center gap-2 font-medium">
            {testRes.ok ? (
              <CheckCircle2 className="h-4 w-4 text-emerald-500" />
            ) : (
              <AlertTriangle className="h-4 w-4 text-rose-500" />
            )}
            <span>
              {testRes.ok
                ? `Connected to ${testRes.provider} (${testRes.available_models.length} models detected)`
                : `Connection failed (${testRes.provider}): ${testRes.error}`}
            </span>
          </div>
          {testRes.warnings?.map((w, i) => (
            <div key={i} className="flex items-start gap-2 text-xs text-amber-700 dark:text-amber-300 pl-6">
              <AlertTriangle className="h-3.5 w-3.5 shrink-0 mt-0.5" />
              <span>{w}</span>
            </div>
          ))}
        </div>
      )}

      {/* Provider Selector */}
      <div className="card p-4 space-y-4">
        <h2 className="text-sm font-semibold text-ink">1. AI Provider</h2>
        <div className="grid gap-2 sm:grid-cols-3">
          {PROVIDER_META.map((p) => {
            const active = form.provider === p.key;
            const hasKey = form.providers[p.key]?.has_key;
            return (
              <button
                key={p.key}
                type="button"
                onClick={() => setForm((s) => (s ? { ...s, provider: p.key } : s))}
                className={`rounded-xl border p-3 text-left transition ${
                  active ? "border-accent bg-accent-soft ring-1 ring-accent" : "border-border bg-bg/40 hover:border-accent/40"
                }`}
              >
                <div className="flex items-center justify-between text-xs font-semibold text-ink">
                  <span>{p.label}</span>
                  {p.needsKey && hasKey && (
                    <span className="rounded bg-emerald-500/15 px-1.5 py-0.5 text-[10px] text-emerald-600 dark:text-emerald-300">
                      Key saved
                    </span>
                  )}
                </div>
                <div className="text-[11px] text-muted mt-0.5 truncate">{p.defaultUrl || "Official Anthropic SDK"}</div>
              </button>
            );
          })}
        </div>

        <div className="grid gap-4 sm:grid-cols-2 pt-2">
          <div className="space-y-1">
            <label htmlFor="prov-url" className="text-xs font-semibold text-muted">
              Endpoint / Base URL
            </label>
            <input
              id="prov-url"
              className="input-field"
              placeholder={provMeta.defaultUrl || "Optional custom base URL"}
              value={provCfg?.base_url ?? ""}
              onChange={(e) =>
                setForm((s) =>
                  s
                    ? {
                        ...s,
                        providers: {
                          ...s.providers,
                          [s.provider]: { ...s.providers[s.provider], base_url: e.target.value },
                        },
                      }
                    : s,
                )
              }
            />
          </div>

          <div className="space-y-1">
            <label htmlFor="prov-key" className="text-xs font-semibold text-muted">
              API Key {provCfg?.has_key ? `(saved: ${provCfg.api_key})` : "(optional for local)"}
            </label>
            <div className="flex gap-2">
              <input
                id="prov-key"
                type="password"
                className="input-field flex-1"
                placeholder={provCfg?.has_key ? "Enter new key to replace saved key…" : "Paste API key…"}
                value={newKeyInput}
                onChange={(e) => setNewKeyInput(e.target.value)}
              />
              {provCfg?.has_key && (
                <button
                  type="button"
                  onClick={() => clearKeyAct.run()}
                  disabled={clearKeyAct.busy}
                  className="btn-secondary text-xs"
                  title="Clear saved API key"
                >
                  <Trash2 className="h-3.5 w-3.5 text-rose-500" /> Clear
                </button>
              )}
            </div>
          </div>
        </div>

        {/* Decode / Context Parameters */}
        <div className="grid gap-3 sm:grid-cols-4 border-t border-border pt-3">
          <div className="space-y-1">
            <label className="text-[11px] font-semibold text-muted">Context Size (num_ctx)</label>
            <input
              type="number"
              min={4096}
              max={262144}
              step={2048}
              className="input-field text-xs"
              value={provCfg?.num_ctx ?? 32768}
              onChange={(e) =>
                setForm((s) =>
                  s
                    ? {
                        ...s,
                        providers: {
                          ...s.providers,
                          [s.provider]: { ...s.providers[s.provider], num_ctx: Number(e.target.value) || 32768 },
                        },
                      }
                    : s,
                )
              }
            />
          </div>
          <div className="space-y-1">
            <label className="text-[11px] font-semibold text-muted">Ollama Repeat Penalty</label>
            <input
              type="number"
              min={1.0}
              max={1.5}
              step={0.02}
              className="input-field text-xs"
              value={provCfg?.repeat_penalty ?? 1.12}
              onChange={(e) =>
                setForm((s) =>
                  s
                    ? {
                        ...s,
                        providers: {
                          ...s.providers,
                          [s.provider]: { ...s.providers[s.provider], repeat_penalty: Number(e.target.value) || 1.12 },
                        },
                      }
                    : s,
                )
              }
            />
          </div>
          <div className="space-y-1">
            <label className="text-[11px] font-semibold text-muted">Top-P / Min-P</label>
            <div className="flex gap-1.5">
              <input
                type="number"
                min={0.1}
                max={1.0}
                step={0.05}
                className="input-field text-xs"
                value={provCfg?.top_p ?? 0.9}
                onChange={(e) =>
                  setForm((s) =>
                    s
                      ? {
                          ...s,
                          providers: {
                            ...s.providers,
                            [s.provider]: { ...s.providers[s.provider], top_p: Number(e.target.value) || 0.9 },
                          },
                        }
                      : s,
                  )
                }
              />
              <input
                type="number"
                min={0.0}
                max={0.5}
                step={0.01}
                className="input-field text-xs"
                value={provCfg?.min_p ?? 0.05}
                onChange={(e) =>
                  setForm((s) =>
                    s
                      ? {
                          ...s,
                          providers: {
                            ...s.providers,
                            [s.provider]: { ...s.providers[s.provider], min_p: Number(e.target.value) || 0.05 },
                          },
                        }
                      : s,
                  )
                }
              />
            </div>
          </div>
          <div className="space-y-1">
            <label className="text-[11px] font-semibold text-muted">Request Timeout (sec)</label>
            <input
              type="number"
              min={30}
              max={900}
              step={15}
              className="input-field text-xs"
              value={provCfg?.timeout_sec ?? 180}
              onChange={(e) =>
                setForm((s) =>
                  s
                    ? {
                        ...s,
                        providers: {
                          ...s.providers,
                          [s.provider]: { ...s.providers[s.provider], timeout_sec: Number(e.target.value) || 180 },
                        },
                      }
                    : s,
                )
              }
            />
          </div>
        </div>
      </div>

      {/* Per-Task Models */}
      <div className="card p-4 space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h2 className="text-sm font-semibold text-ink">2. Per-Task Model Assignment</h2>
            <p className="text-xs text-muted">Your manual choices are never overwritten automatically.</p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {presets?.defaults_by_provider?.[form.provider] && !["ollama", "lmstudio"].includes(form.provider) && (
              <button type="button" onClick={applyProviderDefaults} className="btn-secondary text-xs">
                <Wand2 className="h-3.5 w-3.5" /> Apply {provMeta.label} Preset
              </button>
            )}
            <div className="flex items-center gap-1.5">
              <input
                list="detected-models-list"
                placeholder={availableModels[0] ? `Pick (${availableModels[0]})…` : "Model ID for all…"}
                value={bulkModel}
                onChange={(e) => setBulkModel(e.target.value)}
                className="input-field w-48 py-1 text-xs"
              />
              <button type="button" onClick={() => applyAllModels(bulkModel)} className="btn-secondary text-xs">
                Apply to all
              </button>
            </div>
          </div>
        </div>

        <datalist id="detected-models-list">
          {availableModels.map((m) => (
            <option key={m} value={m} />
          ))}
        </datalist>

        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {Object.entries(TASK_LABELS).map(([role, label]) => {
            const val = form.models[role] ?? "";
            const meta = modelMeta[val];
            return (
              <div key={role} className="space-y-1">
                <div className="flex items-center justify-between">
                  <label htmlFor={`model-${role}`} className="text-xs font-medium text-ink">
                    {label}
                  </label>
                  {meta?.parameter_size && (
                    <span className="text-[10px] rounded bg-surface-2 px-1.5 py-0.5 text-muted">
                      {meta.parameter_size}
                    </span>
                  )}
                </div>
                <input
                  id={`model-${role}`}
                  list="detected-models-list"
                  className="input-field text-xs font-mono"
                  placeholder="Enter or choose model ID…"
                  value={val}
                  onChange={(e) =>
                    setForm((s) => (s ? { ...s, models: { ...s.models, [role]: e.target.value } } : s))
                  }
                />
              </div>
            );
          })}
        </div>
      </div>

      {/* Quality & Automation */}
      <div className="card p-4 space-y-3">
        <h2 className="text-sm font-semibold text-ink">3. Quality Guardrails & Pipeline Automation</h2>
        <div className="grid gap-4 sm:grid-cols-2">
          <label className="flex items-start gap-2.5 text-sm cursor-pointer">
            <input
              type="checkbox"
              checked={form.quality.auto_polish}
              onChange={(e) =>
                setForm((s) => (s ? { ...s, quality: { ...s.quality, auto_polish: e.target.checked } } : s))
              }
              className="mt-1 accent-accent"
            />
            <div>
              <div className="font-medium text-ink">Auto-Polish after drafting</div>
              <div className="text-xs text-muted">Runs Acquiring Editor review + revision + AI-tic line fixer automatically.</div>
            </div>
          </label>

          <label className="flex items-start gap-2.5 text-sm cursor-pointer">
            <input
              type="checkbox"
              checked={form.quality.auto_scene_cards}
              onChange={(e) =>
                setForm((s) => (s ? { ...s, quality: { ...s.quality, auto_scene_cards: e.target.checked } } : s))
              }
              className="mt-1 accent-accent"
            />
            <div>
              <div className="font-medium text-ink">Auto-plan Scene Cards before drafting</div>
              <div className="text-xs text-muted">Prevents plot overruns and paces each chapter accurately.</div>
            </div>
          </label>

          <label className="flex items-start gap-2.5 text-sm cursor-pointer">
            <input
              type="checkbox"
              checked={form.quality.auto_continue_truncated}
              onChange={(e) =>
                setForm((s) =>
                  s ? { ...s, quality: { ...s.quality, auto_continue_truncated: e.target.checked } } : s,
                )
              }
              className="mt-1 accent-accent"
            />
            <div>
              <div className="font-medium text-ink">Auto-continue truncated chapters</div>
              <div className="text-xs text-muted">Seamlessly finishes output if the model stops at token limit.</div>
            </div>
          </label>

          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <label className="text-xs font-medium text-muted">Max Polish Passes</label>
              <input
                type="number"
                min={1}
                max={5}
                className="input-field text-xs"
                value={form.quality.max_polish_passes}
                onChange={(e) =>
                  setForm((s) =>
                    s
                      ? { ...s, quality: { ...s.quality, max_polish_passes: Math.max(1, Number(e.target.value) || 1) } }
                      : s,
                  )
                }
              />
            </div>
            <div className="space-y-1">
              <label className="text-xs font-medium text-muted">Target Tells / 1,000w</label>
              <input
                type="number"
                min={0}
                max={30}
                step={0.5}
                className="input-field text-xs"
                value={form.quality.tells_threshold}
                onChange={(e) =>
                  setForm((s) =>
                    s
                      ? { ...s, quality: { ...s.quality, tells_threshold: Number(e.target.value) || 0 } }
                      : s,
                  )
                }
              />
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
