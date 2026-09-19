import * as React from "react";
import { Badge } from "../../components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "../../components/ui/card";
import { getEvaClient } from "../../api/client";
import type { FocusSession, FocusCompletionSummary, FocusStopResponse } from "../../api/types.generated";
import { useEvaQuery } from "../../hooks/useEvaQuery";
import { formatTimeInWarsaw } from "../../lib/format";

type FocusThreshold = "high" | "medium" | "low";

interface FocusPanelProps {
  onSessionChange?: (session: FocusSession | null) => void;
}

function formatThreshold(threshold: FocusThreshold): { label: string; className: string } {
  switch (threshold) {
    case "high":
      return { label: "High only", className: "bg-danger/10 text-danger border-danger/30" };
    case "medium":
      return { label: "Medium +", className: "bg-primary/10 text-primary border-primary/30" };
    default:
      return { label: "All", className: "bg-muted text-muted-foreground border-border/60" };
  }
}

function FocusStartForm({ onStart }: { onStart: (duration: number, threshold: FocusThreshold, senderOverrides: string[]) => Promise<void> }) {
  const [duration, setDuration] = React.useState(60);
  const [threshold, setThreshold] = React.useState<FocusThreshold>("medium");
  const [senderOverrides, setSenderOverrides] = React.useState("");
  const [isLoading, setIsLoading] = React.useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsLoading(true);
    try {
      const overrides = senderOverrides
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean);
      await onStart(duration, threshold, overrides);
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <div>
        <label htmlFor="focus-duration" className="block text-[12px] font-medium text-muted-foreground mb-1">
          Duration (minutes)
        </label>
        <input
          id="focus-duration"
          type="number"
          min={15}
          max={240}
          step={15}
          value={duration}
          onChange={(e) => setDuration(Number(e.target.value))}
          className="w-full rounded-control border border-border-strong bg-background px-3 py-2 text-[13.5px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        />
      </div>

      <div>
        <label htmlFor="focus-threshold" className="block text-[12px] font-medium text-muted-foreground mb-1">
          Priority threshold
        </label>
        <select
          id="focus-threshold"
          value={threshold}
          onChange={(e) => setThreshold(e.target.value as FocusThreshold)}
          className="w-full rounded-control border border-border-strong bg-background px-3 py-2 text-[13.5px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <option value="high">High only</option>
          <option value="medium">Medium and above</option>
          <option value="low">All priorities</option>
        </select>
      </div>

      <div>
        <label htmlFor="focus-senders" className="block text-[12px] font-medium text-muted-foreground mb-1">
          Exact sender overrides (comma-separated emails)
        </label>
        <input
          id="focus-senders"
          type="text"
          value={senderOverrides}
          onChange={(e) => setSenderOverrides(e.target.value)}
          placeholder="cfo.demo@example.com, partner@company.com"
          className="w-full rounded-control border border-border-strong bg-background px-3 py-2 text-[13.5px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        />
        <p className="mt-1 text-[11.5px] text-subtle-foreground">
          Only exact email addresses are accepted. No domain/topic wildcards.
        </p>
      </div>

      <button
        type="submit"
        disabled={isLoading}
        className="w-full rounded-control bg-primary px-4 py-2 text-[13.5px] font-medium text-primary-foreground transition-colors hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
      >
        {isLoading ? "Starting…" : "Start Focus"}
      </button>
    </form>
  );
}

function ActiveFocusDisplay({ session, onStop }: { session: FocusSession; onStop: () => Promise<FocusStopResponse> }) {
  const thresholdStyle = formatThreshold(session.threshold);
  const [isStopping, setIsStopping] = React.useState(false);
  const [stopResult, setStopResult] = React.useState<FocusStopResponse | null>(null);

  const handleStop = async () => {
    setIsStopping(true);
    try {
      const result = await onStop();
      setStopResult(result);
    } finally {
      setIsStopping(false);
    }
  };

  return (
    <div className="space-y-4">
      {stopResult && (
        <FocusCompletionSummaryDisplay summary={stopResult.summary} onClose={() => setStopResult(null)} />
      )}

      {!stopResult && (
        <>
          <div className="flex items-center gap-2">
            <span className="inline-flex items-center size-2 rounded-full bg-green-500" aria-hidden />
            <span className="text-[13.5px] font-medium">Focus active</span>
            <Badge variant="outline" className={thresholdStyle.className}>
              {thresholdStyle.label}
            </Badge>
          </div>
          <div className="space-y-2 text-[13px] text-muted-foreground">
            <p>Until {formatTimeInWarsaw(session.ends_at)}</p>
            {session.sender_overrides && session.sender_overrides.length > 0 && (
              <p>
                Sender overrides: {session.sender_overrides.join(", ")}
              </p>
            )}
          </div>
          <button
            type="button"
            onClick={handleStop}
            disabled={isStopping}
            className="w-full rounded-control border border-danger text-danger px-4 py-2 text-[13.5px] font-medium transition-colors hover:bg-danger/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-danger disabled:opacity-50"
          >
            {isStopping ? "Stopping…" : "Stop Focus"}
          </button>
        </>
      )}
    </div>
  );
}

function FocusCompletionSummaryDisplay({ summary, onClose }: { summary: FocusCompletionSummary; onClose: () => void }) {
  return (
    <Card className="border-green/30 bg-green-50/30 dark:bg-green-950/10">
      <CardHeader className="flex items-center justify-between">
        <CardTitle className="text-[14px] flex items-center gap-2">
          <span className="text-green-600 dark:text-green-400">✅</span>
          Focus Completed
        </CardTitle>
        <button
          type="button"
          onClick={onClose}
          className="text-muted-foreground hover:text-foreground"
          aria-label="Dismiss"
        >
          ✕
        </button>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="grid grid-cols-2 gap-4 text-center">
          <Stat label="Total received" value={summary.total_received} />
          <Stat label="Deferred" value={summary.deferred_count} />
          <Stat label="Decisions" value={summary.decision_count} />
          <Stat label="Actions" value={summary.action_count} />
          <Stat label="FYI" value={summary.fyi_count} />
        </div>
        {summary.attention_item_ids && summary.attention_item_ids.length > 0 && (
          <details>
            <summary className="cursor-pointer text-[12.5px] text-primary">Attention item IDs ({summary.attention_item_ids.length})</summary>
            <ul className="mt-2 space-y-1 pl-4 list-disc text-[12px] text-muted-foreground">
              {summary.attention_item_ids.map((id) => (
                <li key={id} className="font-mono">{id}</li>
              ))}
            </ul>
          </details>
        )}
      </CardContent>
    </Card>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="p-3 rounded-lg bg-background border border-border/60">
      <p className="text-[11px] font-medium uppercase tracking-[0.06em] text-subtle-foreground">{label}</p>
      <p className="mt-1 text-[20px] font-bold tabular-nums">{value}</p>
    </div>
  );
}

function FocusOff({ onStart }: { onStart: (duration: number, threshold: FocusThreshold, senderOverrides: string[]) => Promise<void> }) {
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <span className="inline-flex items-center size-2 rounded-full bg-muted" aria-hidden />
        <span className="text-[13.5px] font-medium text-muted-foreground">Focus off</span>
      </div>
      <p className="text-[13px] text-muted-foreground">
        Start a Focus session to defer lower-priority items and only allow high-priority or overridden senders through.
      </p>
      <FocusStartForm onStart={onStart} />
    </div>
  );
}

export function FocusPanel({ onSessionChange }: FocusPanelProps) {
  const [reloadKey, setReloadKey] = React.useState(0);
  const client = getEvaClient();
  const focus = useEvaQuery(`focus:${reloadKey}`, (c) => c.getCurrentFocus(), client);

  const handleStart = async (duration: number, threshold: FocusThreshold, senderOverrides: string[]) => {
    const response = await client.startFocus(duration, threshold, senderOverrides);
    setReloadKey((n) => n + 1);
    if (response.session && onSessionChange) {
      onSessionChange(response.session);
    }
  };

  const handleStop = async () => {
    const response = await client.stopFocus();
    setReloadKey((n) => n + 1);
    if (onSessionChange) {
      onSessionChange(response.session ?? null);
    }
    return response;
  };

  const session = focus.status === "ready" ? focus.data?.session ?? null : null;

  if (focus.status === "loading") {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-[14px] flex items-center gap-2">
            <span className="text-muted-foreground">🎯</span>
            Focus
          </CardTitle>
        </CardHeader>
        <CardContent>
          <p className="py-2 text-[13px] text-muted-foreground">Loading Focus…</p>
        </CardContent>
      </Card>
    );
  }

  if (focus.status === "error") {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-[14px] flex items-center gap-2">
            <span className="text-muted-foreground">🎯</span>
            Focus
          </CardTitle>
        </CardHeader>
        <CardContent>
          <p className="py-2 text-[13px] text-danger" role="alert">
            Focus unavailable: {focus.error?.message}
          </p>
          <button
            type="button"
            onClick={() => setReloadKey((n) => n + 1)}
            className="mt-3 rounded-control border border-border-strong px-3 py-1 text-[12px] font-medium text-foreground transition-colors hover:bg-accent"
          >
            Retry
          </button>
        </CardContent      >
    </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-[14px] flex items-center gap-2">
          <span className="text-muted-foreground">🎯</span>
          Focus
        </CardTitle>
      </CardHeader>
      <CardContent>
        {session ? (
          <ActiveFocusDisplay session={session} onStop={handleStop} />
        ) : (
          <FocusOff onStart={handleStart} />
        )}
      </CardContent>
    </Card>
  );
}