import * as React from "react";
import { Badge } from "../../components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "../../components/ui/card";
import { getEvaClient } from "../../api/client";
import type {
  FocusSession,
  FocusStopResponse,
  AttentionPriority,
  FocusStartRequest,
} from "../../api/types.generated";
import { useEvaQuery } from "../../hooks/useEvaQuery";
import { formatTimeInWarsaw } from "../../lib/format";

interface FocusPanelProps {
  onSessionChange?: (session: FocusSession | null) => void;
}

/** Remaining time as m:ss (or h:mm:ss past an hour). Pure, so it is unit-testable. */
export function formatRemaining(remainingMs: number): string {
  const total = Math.max(0, Math.floor(remainingMs / 1000));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  const mm = String(minutes).padStart(2, "0");
  const ss = String(seconds).padStart(2, "0");
  return hours > 0 ? `${hours}:${mm}:${ss}` : `${mm}:${ss}`;
}

/** Elapsed share of the session window, clamped to 0–100. */
export function sessionProgressPct(startsAt: string, endsAt: string, nowMs: number): number {
  const start = Date.parse(startsAt);
  const end = Date.parse(endsAt);
  if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) return 0;
  return Math.min(100, Math.max(0, ((nowMs - start) / (end - start)) * 100));
}

/**
 * Ticks once a second against the session's own end timestamp (never a locally
 * accumulated counter, so a backgrounded tab or a re-render cannot drift).
 * Fires `onEnd` exactly once per session when the remaining time reaches zero.
 */
function useCountdownTo(endsAt: string | undefined, onEnd?: () => void): number {
  const [nowMs, setNowMs] = React.useState(() => Date.now());
  const onEndRef = React.useRef(onEnd);
  const firedRef = React.useRef(false);

  React.useEffect(() => {
    onEndRef.current = onEnd;
  }, [onEnd]);

  React.useEffect(() => {
    if (!endsAt) return undefined;
    firedRef.current = false;
    const tick = () => {
      setNowMs(Date.now());
      if (!firedRef.current && Date.parse(endsAt) - Date.now() <= 0) {
        firedRef.current = true;
        onEndRef.current?.();
      }
    };
    tick();
    const timer = window.setInterval(tick, 1000);
    return () => window.clearInterval(timer);
  }, [endsAt]);

  return endsAt ? Date.parse(endsAt) - nowMs : 0;
}

function formatThreshold(threshold: AttentionPriority): { label: string; className: string } {
  switch (threshold) {
    case "high":
      return { label: "High only", className: "bg-danger/10 text-danger border-danger/30" };
    case "medium":
      return { label: "Medium +", className: "bg-primary/10 text-primary border-primary/30" };
    default:
      return { label: "All", className: "bg-muted text-muted-foreground border-border/60" };
  }
}

function FocusStartForm({ onStart }: { onStart: (request: FocusStartRequest) => Promise<void> }) {
  const [duration, setDuration] = React.useState(60);
  const [threshold, setThreshold] = React.useState<AttentionPriority>("medium");
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
      const request: FocusStartRequest = {
        duration_minutes: duration,
        threshold,
        sender_overrides: overrides.length > 0 ? overrides : undefined,
      };
      await onStart(request);
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
          onChange={(e) => setThreshold(e.target.value as AttentionPriority)}
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

function ActiveFocusDisplay({
  session,
  onStop,
  onNaturalEnd,
}: {
  session: FocusSession;
  onStop: () => Promise<void>;
  onNaturalEnd: (session: FocusSession) => void;
}) {
  const thresholdStyle = formatThreshold(session.threshold);
  const [isStopping, setIsStopping] = React.useState(false);
  const remainingMs = useCountdownTo(session.ends_at, () => onNaturalEnd(session));
  const progress = sessionProgressPct(session.starts_at, session.ends_at, Date.now());

  const handleStop = async () => {
    setIsStopping(true);
    try {
      await onStop();
    } finally {
      setIsStopping(false);
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <span className="inline-flex items-center size-2 rounded-full bg-success" aria-hidden />
        <span className="text-[13.5px] font-medium">Focus active</span>
        <Badge variant="outline" className={thresholdStyle.className}>
          {thresholdStyle.label}
        </Badge>
      </div>

      {/* Live countdown: the ticking digits are decorative for screen readers,
          which get the stable "Until …" line below instead. */}
      <div className="rounded-card border border-border/60 bg-muted/40 px-4 py-3">
        <div className="flex items-baseline justify-between gap-3">
          <p className="text-[11px] font-medium uppercase tracking-[0.06em] text-subtle-foreground">
            Time remaining
          </p>
          <p
            role="timer"
            aria-live="off"
            className="font-mono text-[28px] font-semibold leading-none tabular-nums"
          >
            {formatRemaining(remainingMs)}
          </p>
        </div>
        <div
          role="progressbar"
          aria-label="Focus session progress"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(progress)}
          className="mt-3 h-1.5 w-full overflow-hidden rounded-full bg-border/60"
        >
          <div
            className="h-full rounded-full bg-primary transition-[width] duration-1000 ease-linear"
            style={{ width: `${progress}%` }}
          />
        </div>
        <p className="mt-2 text-[12px] text-muted-foreground">
          Until {formatTimeInWarsaw(session.ends_at)} · started {formatTimeInWarsaw(session.starts_at)}
        </p>
      </div>

      {session.sender_overrides && session.sender_overrides.length > 0 && (
        <p className="text-[13px] text-muted-foreground">Sender overrides: {session.sender_overrides.join(", ")}</p>
      )}
      <button
        type="button"
        onClick={handleStop}
        disabled={isStopping}
        className="w-full rounded-control border border-danger text-danger px-4 py-2 text-[13.5px] font-medium transition-colors hover:bg-danger/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-danger disabled:opacity-50"
      >
        {isStopping ? "Stopping…" : "Stop Focus"}
      </button>
    </div>
  );
}

function FocusCompletionSummaryDisplay({ summary, onClose }: { summary: FocusStopResponse["summary"]; onClose: () => void }) {
  return (
    <Card className="border-success/30 bg-success/5 dark:bg-success/10">
      <CardHeader className="flex items-center justify-between">
        <CardTitle className="text-[14px] flex items-center gap-2">
          <span className="text-success">✅</span>
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

function FocusOff({ onStart }: { onStart: (request: FocusStartRequest) => Promise<void> }) {
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
  const [stopResult, setStopResult] = React.useState<FocusStopResponse | null>(null);
  // Stable, sanitized mutation error state: raw provider/stack/body text is
  // never shown, and the previous error clears on the next success.
  const [mutationError, setMutationError] = React.useState<string | null>(null);
  const client = getEvaClient();
  const focus = useEvaQuery(`focus:${reloadKey}`, (c) => c.getCurrentFocus(), client);

  const handleStart = async (request: FocusStartRequest) => {
    try {
      const response = await client.startFocus(request);
      setMutationError(null);
      setStopResult(null);
      setReloadKey((n) => n + 1);
      if (response.session && onSessionChange) {
        onSessionChange(response.session);
      }
    } catch (error) {
      console.error("Failed to start Focus:", error);
      // Focus remains OFF; the existing form is the retry path.
      setMutationError("Focus could not be started. Try again.");
    }
  };

  const handleStop = async () => {
    try {
      const response = await client.stopFocus();
      setMutationError(null);
      setStopResult(response);
      setReloadKey((n) => n + 1);
      if (onSessionChange) {
        onSessionChange(null);
      }
    } catch (error) {
      console.error("Failed to stop Focus:", error);
      // The current session remains visibly ACTIVE and the Stop button stays
      // usable for a retry.
      setMutationError("Focus could not be stopped. Focus is still active.");
    }
  };

  /**
   * The countdown reached zero: ask the backend for the summary of the session
   * that just ended and show it, exactly as an explicit Stop would.
   */
  const handleNaturalEnd = React.useCallback(
    async (ended: FocusSession) => {
      try {
        const { summary } = await client.getFocusSummary(ended.id);
        setStopResult({ session: ended, summary });
      } catch {
        // No summary available is not an error state: the panel falls back to
        // the normal "Focus off" view after the refetch below.
        setStopResult(null);
      } finally {
        setReloadKey((n) => n + 1);
        if (onSessionChange) onSessionChange(null);
      }
    },
    [client, onSessionChange],
  );

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
        </CardContent>
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
        {mutationError && (
          <p role="alert" className="mb-4 rounded-control border border-danger/30 bg-danger/5 px-3 py-2 text-[13px] text-danger">
            {mutationError}
          </p>
        )}
        {session ? (
          <ActiveFocusDisplay session={session} onStop={handleStop} onNaturalEnd={handleNaturalEnd} />
        ) : stopResult ? (
          <FocusCompletionSummaryDisplay summary={stopResult.summary} onClose={() => setStopResult(null)} />
        ) : (
          <FocusOff onStart={handleStart} />
        )}
      </CardContent>
    </Card>
  );
}