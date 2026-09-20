import * as React from "react";
import { Badge } from "../components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "../components/ui/card";
import { Separator } from "../components/ui/separator";
import { getEvaClient } from "../api/client";
import type {
  LlmSettingsResponse,
  LlmSettingsUpdateRequest,
  LlmTestConnectionResponse,
  LLMModelInfo,
  HealthStatus,
} from "../api/types.generated";
import { useEvaQuery } from "../hooks/useEvaQuery";

/**
 * AI Engine Settings — mock-first slice against the frozen LlmSettings
 * contracts. Sanitized responses only: the frontend never receives, stores,
 * or renders an actual API key, only presence flags. The backend remains
 * authoritative for readiness (`configured`) and origin allowlisting.
 */

const inputClass =
  "w-full rounded-control border border-border-strong bg-background px-3 py-2 text-[13.5px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

function healthBadge(status: HealthStatus): { label: string; className: string } {
  switch (status) {
    case "ready":
      return { label: "Ready", className: "bg-success/10 text-success border-success/30" };
    case "degraded":
      return { label: "Degraded", className: "bg-warning/10 text-warning border-warning/30" };
    default:
      return { label: "Unavailable", className: "bg-danger/10 text-danger border-danger/30" };
  }
}

function HealthLine({
  health,
  testedModel,
}: {
  health: NonNullable<LlmTestConnectionResponse["health"]>;
  testedModel?: string | null;
}) {
  const badge = healthBadge(health.status);
  // Precedence: top-level response model first, then health.model; render the
  // single tested model once — never a duplicate label, never an invented one.
  const model = testedModel ?? health.model ?? null;
  return (
    <div className="flex flex-wrap items-center gap-2 text-[13px]">
      <span className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-[11px] font-medium ${badge.className}`}>
        {badge.label}
      </span>
      {health.provider && <span className="text-muted-foreground">{health.provider}</span>}
      {model && <span className="font-mono text-[12px]">{model}</span>}
      {health.detail && <span className="text-muted-foreground">{health.detail}</span>}
    </div>
  );
}

function SettingsLoading() {
  return (
    <div className="mx-auto w-full max-w-3xl px-6 py-8 lg:px-8 lg:py-10" aria-busy="true" aria-live="polite">
      <div className="h-4 w-48 motion-safe:animate-pulse rounded-full bg-muted" />
      <div className="mt-3 h-8 w-72 motion-safe:animate-pulse rounded-full bg-muted" />
      <div className="mt-8 space-y-4">
        {[...Array(4)].map((_, i) => (
          <div key={i} className="h-28 motion-safe:animate-pulse rounded-card bg-muted" />
        ))}
      </div>
      <p className="mt-6 text-[12.5px] text-muted-foreground">Loading AI engine settings…</p>
    </div>
  );
}

function SettingsError({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="mx-auto w-full max-w-md px-6 py-24 text-center">
      <h2 className="text-[17px] font-semibold tracking-tight">Settings unavailable</h2>
      <p className="mt-2 text-[13.5px] leading-relaxed text-muted-foreground">
        AI engine settings could not be loaded. Nothing was faked in its place —
        retry when the connection to the EVA backend is available.
      </p>
      <button
        type="button"
        onClick={onRetry}
        className="mt-6 rounded-control bg-primary px-4 py-2 text-[13.5px] font-medium text-primary-foreground transition-colors hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
      >
        Retry
      </button>
    </div>
  );
}

function capabilityLabel(value: boolean | null | undefined): string {
  if (value === null || value === undefined) return "Unknown";
  return value ? "Yes" : "No";
}

export default function Settings() {
  const [reloadKey, setReloadKey] = React.useState(0);
  const client = getEvaClient();
  const settings = useEvaQuery(`llm-settings:${reloadKey}`, (c) => c.getLlmSettings(), client);
  const retry = () => setReloadKey((n) => n + 1);

  // Form state
  const [baseUrl, setBaseUrl] = React.useState("");
  const [model, setModel] = React.useState("");
  const [apiKey, setApiKey] = React.useState("");
  const [fallbackBaseUrl, setFallbackBaseUrl] = React.useState("");
  const [fallbackModel, setFallbackModel] = React.useState("");
  const [fallbackApiKey, setFallbackApiKey] = React.useState("");
  const [hydratedFrom, setHydratedFrom] = React.useState<LlmSettingsResponse | null>(null);

  // Operation state
  const [saving, setSaving] = React.useState(false);
  const [saveError, setSaveError] = React.useState<string | null>(null);
  const [savedAt, setSavedAt] = React.useState<number | null>(null);
  const [detecting, setDetecting] = React.useState(false);
  const [detectedModels, setDetectedModels] = React.useState<LLMModelInfo[] | null>(null);
  const [detectError, setDetectError] = React.useState<string | null>(null);
  const [testing, setTesting] = React.useState(false);
  const [testResult, setTestResult] = React.useState<LlmTestConnectionResponse | null>(null);
  const [testError, setTestError] = React.useState<string | null>(null);

  const data = settings.status === "ready" ? settings.data : null;

  // Suppresses the dirty-effect clearing of the Saved indicator while a
  // post-save rehydration is still in flight (the form briefly differs from
  // the stale query data during that gap).
  const pendingRehydrateRef = React.useRef(false);

  // Hydrate the form once per loaded response; blank password fields never
  // receive or echo an existing secret.
  React.useEffect(() => {
    if (data && hydratedFrom !== data) {
      pendingRehydrateRef.current = true;
      setBaseUrl(data.base_url ?? "");
      setModel(data.model ?? "");
      setApiKey("");
      setFallbackBaseUrl(data.fallback_base_url ?? "");
      setFallbackModel(data.fallback_model ?? "");
      setFallbackApiKey("");
      setHydratedFrom(data);
    } else if (data) {
      pendingRehydrateRef.current = false;
    }
  }, [data, hydratedFrom]);

  const dirty =
    !!data &&
    (baseUrl !== (data.base_url ?? "") ||
      model !== (data.model ?? "") ||
      apiKey.length > 0 ||
      fallbackBaseUrl !== (data.fallback_base_url ?? "") ||
      fallbackModel !== (data.fallback_model ?? "") ||
      fallbackApiKey.length > 0);

  const validation =
    model.trim().length === 0 ? "Model ID is required."
    : baseUrl.length > 0 && !/^https?:\/\//.test(baseUrl) ? "Base URL must begin with http:// or https://."
    : null;

  // A cleared Base URL must be sent as null (the frozen contract is
  // `string | null` and the backend rejects ""), never as an empty string.
  const clearSavedIndicatorIfDirty = () => {
    if (!pendingRehydrateRef.current) setSavedAt(null);
  };

  React.useEffect(() => {
    if (dirty) clearSavedIndicatorIfDirty();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dirty]);

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!client || validation || !dirty) return;
    setSaving(true);
    setSaveError(null);
    setSavedAt(null);
    try {
      const request: LlmSettingsUpdateRequest = {};
      // Omit unchanged values — a blank input must never clear an existing
      // key or clobber saved fields. A CLEARED Base URL is sent as null
      // (the frozen contract is `string | null`; "" would be rejected by
      // the backend validator), consistent with the fallback field.
      if (baseUrl !== (data?.base_url ?? "")) request.base_url = baseUrl || null;
      if (model !== (data?.model ?? "")) request.model = model;
      if (apiKey.length > 0) request.api_key = apiKey;
      if (fallbackBaseUrl !== (data?.fallback_base_url ?? "")) request.fallback_base_url = fallbackBaseUrl || null;
      if (fallbackModel !== (data?.fallback_model ?? "")) request.fallback_model = fallbackModel || null;
      if (fallbackApiKey.length > 0) request.fallback_api_key = fallbackApiKey;
      const response = await client.updateLlmSettings(request);
      setSavedAt(Date.now());
      setApiKey("");
      setFallbackApiKey("");
      // On a successful save the configuration changed: stale operation
      // results from the previous configuration no longer describe it.
      setDetectedModels(null);
      setDetectError(null);
      setTestResult(null);
      setTestError(null);
      setHydratedFrom(null);
      pendingRehydrateRef.current = true;
      // Refresh sanitized view from the response (never echoes the key).
      setReloadKey((n) => n + 1);
      void response;
    } catch (error) {
      // A failed save keeps the typed replacement key in transient React
      // state so the user can retry; it is never persisted anywhere.
      setSaveError(error instanceof Error ? error.message : "Save failed");
    } finally {
      setSaving(false);
    }
  };

  const handleDetect = async () => {
    setDetecting(true);
    setDetectError(null);
    try {
      const response = await client.detectLlmModels();
      setDetectedModels(response.models);
    } catch (error) {
      setDetectError(error instanceof Error ? error.message : "Detect failed");
    } finally {
      setDetecting(false);
    }
  };

  const handleTest = async () => {
    setTesting(true);
    setTestError(null);
    try {
      const response = await client.testLlmConnection();
      setTestResult(response);
    } catch (error) {
      setTestResult(null);
      setTestError(error instanceof Error ? error.message : "Connection test failed");
    } finally {
      setTesting(false);
    }
  };

  if (settings.status === "loading") return <SettingsLoading />;
  if (settings.status === "error" || !data) {
    return <SettingsError onRetry={retry} />;
  }

  return (
    <div className="mx-auto w-full max-w-3xl px-6 py-8 lg:px-8 lg:py-10">
      <header className="mb-6">
        <h1 className="text-[26px] font-semibold leading-tight tracking-[-0.02em]">AI Engine</h1>
        <p className="mt-1.5 text-[13.5px] leading-relaxed text-muted-foreground">
          Primary self-hosted inference configuration. The EVA backend is
          authoritative for readiness and origin allowlisting.
        </p>
      </header>

      <form onSubmit={handleSave} className="space-y-6">
        {/* Status strip */}
        <div className="flex flex-wrap items-center gap-3 text-[13px]">
          <span className="text-muted-foreground">Provider:</span>
          <span className="font-medium">{data.provider || "—"}</span>
          <Separator orientation="vertical" className="h-4" />
          <span className="text-muted-foreground">Configured:</span>
          {data.configured ? (
            <Badge variant="default">Configured</Badge>
          ) : (
            <Badge variant="warning">Not configured</Badge>
          )}
        </div>

        {/* Primary self-hosted engine */}
        <Card>
          <CardHeader>
            <CardTitle className="text-[14px]">Primary self-hosted engine</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div>
              <label htmlFor="settings-base-url" className="block text-[12px] font-medium text-muted-foreground mb-1">
                Base URL
              </label>
              <input
                id="settings-base-url"
                type="text"
                value={baseUrl}
                onChange={(e) => setBaseUrl(e.target.value)}
                placeholder="http://host.tailnet.example:8000/v1"
                autoComplete="off"
                className={inputClass}
              />
            </div>
            <div>
              <label htmlFor="settings-model" className="block text-[12px] font-medium text-muted-foreground mb-1">
                Model ID
              </label>
              <input
                id="settings-model"
                type="text"
                value={model}
                onChange={(e) => setModel(e.target.value)}
                placeholder="actual server-exposed model ID"
                autoComplete="off"
                className={inputClass}
              />
            </div>
            <div>
              <label htmlFor="settings-api-key" className="block text-[12px] font-medium text-muted-foreground mb-1">
                API key {data.api_key_present ? "(configured)" : "(not set)"}
              </label>
              <input
                id="settings-api-key"
                type="password"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                placeholder={data.api_key_present ? "API key configured" : ""}
                autoComplete="new-password"
                className={inputClass}
              />
              <p className="mt-1 text-[11.5px] text-subtle-foreground">
                {data.api_key_present
                  ? "An API key is configured. Typing here replaces it; leaving this empty keeps the existing key."
                  : "No API key configured. The key is sent once to the backend and never displayed again."}
              </p>
            </div>
          </CardContent>
        </Card>

        {/* Optional fallback */}
        <Card>
          <CardHeader>
            <CardTitle className="text-[14px]">Optional self-hosted fallback</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <p className="text-[12.5px] text-muted-foreground">
              Fallback remains self-hosted/private and is not automatic cloud recovery.
            </p>
            <div>
              <label htmlFor="settings-fallback-base-url" className="block text-[12px] font-medium text-muted-foreground mb-1">
                Fallback base URL
              </label>
              <input
                id="settings-fallback-base-url"
                type="text"
                value={fallbackBaseUrl}
                onChange={(e) => setFallbackBaseUrl(e.target.value)}
                placeholder="http://backup.tailnet.example:8000/v1"
                autoComplete="off"
                className={inputClass}
              />
            </div>
            <div>
              <label htmlFor="settings-fallback-model" className="block text-[12px] font-medium text-muted-foreground mb-1">
                Fallback model ID
              </label>
              <input
                id="settings-fallback-model"
                type="text"
                value={fallbackModel}
                onChange={(e) => setFallbackModel(e.target.value)}
                placeholder="actual fallback model ID"
                autoComplete="off"
                className={inputClass}
              />
            </div>
            <div>
              <label htmlFor="settings-fallback-api-key" className="block text-[12px] font-medium text-muted-foreground mb-1">
                Fallback API key {data.fallback_api_key_present ? "(configured)" : "(not set)"}
              </label>
              <input
                id="settings-fallback-api-key"
                type="password"
                value={fallbackApiKey}
                onChange={(e) => setFallbackApiKey(e.target.value)}
                placeholder={data.fallback_api_key_present ? "Fallback API key configured" : ""}
                autoComplete="new-password"
                className={inputClass}
              />
            </div>
          </CardContent>
        </Card>

        {validation && (
          <p className="text-[12.5px] text-danger" role="alert">{validation}</p>
        )}

        <div className="flex flex-wrap items-center gap-3">
          <button
            type="submit"
            disabled={saving || !!validation || !dirty}
            className="rounded-control bg-primary px-4 py-2 text-[13.5px] font-medium text-primary-foreground transition-colors hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
          >
            {saving ? "Saving…" : "Save settings"}
          </button>
          <button
            type="button"
            onClick={handleDetect}
            disabled={detecting || dirty}
            className="rounded-control border border-border-strong px-4 py-2 text-[13.5px] font-medium text-foreground transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
          >
            {detecting ? "Detecting…" : "Detect models"}
          </button>
          <button
            type="button"
            onClick={handleTest}
            disabled={testing || dirty}
            className="rounded-control border border-border-strong px-4 py-2 text-[13.5px] font-medium text-foreground transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
          >
            {testing ? "Testing…" : "Test connection"}
          </button>
          {dirty && (
            <span className="text-[12px] text-subtle-foreground">
              Detect/test use saved settings — save first.
            </span>
          )}
          {savedAt !== null && (
            <span className="text-[12.5px] text-success" role="status" aria-live="polite">Saved</span>
          )}
        </div>

        {saveError && (
          <p className="text-[13px] text-danger" role="alert">Save failed: {saveError}</p>
        )}

        {/* Test connection result */}
        <div aria-live="polite">
          {testError && (
            <p className="text-[13px] text-danger" role="alert">
              Connection test failed: {testError}
            </p>
          )}
          {testResult?.health && (
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-[12px] uppercase tracking-[0.06em] text-subtle-foreground">Last test</span>
              <HealthLine health={testResult.health} testedModel={testResult.model ?? null} />
              {testResult.latency_ms != null && (
                <span className="text-[12.5px] text-muted-foreground">{testResult.latency_ms} ms</span>
              )}
            </div>
          )}
          {testResult && testResult.latency_ms == null && (
            <p className="text-[11.5px] text-subtle-foreground">Latency not reported.</p>
          )}
        </div>

        {/* Detected models */}
        <div aria-live="polite">
          {detectError && (
            <p className="text-[13px] text-danger" role="alert">Model detection failed: {detectError}</p>
          )}
          {detectedModels && detectedModels.length > 0 && (
            <Card>
              <CardHeader>
                <CardTitle className="text-[14px]">Detected models (saved settings)</CardTitle>
              </CardHeader>
              <CardContent>
                <ul className="divide-y divide-border/60">
                  {detectedModels.map((m) => (
                    <li key={m.id} className="flex flex-wrap items-center gap-3 py-3">
                      <span className="font-mono text-[12.5px]">{m.id}</span>
                      {m.display_name && m.display_name !== m.id && (
                        <span className="text-[12.5px] text-muted-foreground">{m.display_name}</span>
                      )}
                      <Badge variant="outline">Tools: {capabilityLabel(m.supports_tools)}</Badge>
                      <Badge variant="outline">Structured: {capabilityLabel(m.supports_structured_output)}</Badge>
                      <span className="text-[11.5px] text-subtle-foreground">
                        Context: {m.context_window == null ? "Unknown" : m.context_window.toLocaleString("en-US")}
                      </span>
                      <button
                        type="button"
                        onClick={() => setModel(m.id)}
                        className="ml-auto rounded-control border border-border-strong px-3 py-1 text-[12px] font-medium text-foreground transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                      >
                        Use
                      </button>
                    </li>
                  ))}
                </ul>
              </CardContent>
            </Card>
          )}
        </div>
      </form>

      {/* Optional cloud inference — read-only status */}
      <Card className="mt-6">
        <CardHeader>
          <CardTitle className="text-[14px]">Optional cloud inference</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2 text-[13px]">
          <p>
            Cloud inference:{" "}
            <span className="font-medium">{data.allow_cloud_inference ? "Enabled" : "Disabled"}</span>
            {" · "}
            Workspace-derived cloud inference:{" "}
            <span className="font-medium">{data.allow_workspace_cloud_inference ? "Enabled" : "Disabled"}</span>
          </p>
          <p className="text-[12.5px] text-muted-foreground">
            Cloud inference is optional and never automatic recovery. These
            permissions are backend-managed in the current MVP; the mandatory
            path remains self-hosted.
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
