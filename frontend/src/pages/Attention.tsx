import * as React from "react";
import { Badge } from "../components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "../components/ui/card";
import { Separator } from "../components/ui/separator";
import { getEvaClient } from "../api/client";
import type { AttentionItem, AttentionExplanationResponse, Reason, SourceRef } from "../api/types.generated";
import { useEvaQuery } from "../hooks/useEvaQuery";
import { formatDeadline, formatTimeInWarsaw } from "../lib/format";

type AttentionDetailProps = {
  item: AttentionItem;
  explanation: AttentionExplanationResponse;
};

function AttentionDetail({ item, explanation }: AttentionDetailProps) {
  const priorityStyles: Record<AttentionItem["priority"], string> = {
    high: "bg-danger/10 text-danger border-danger/30",
    medium: "bg-primary/10 text-primary border-primary/30",
    low: "bg-muted text-muted-foreground border-border/60",
  };

  return (
    <div className="mx-auto w-full max-w-3xl px-6 py-8" role="dialog" aria-labelledby="attention-detail-title" aria-modal="true">
      <button
        type="button"
        className="absolute right-6 top-6 rounded-full p-1 text-muted-foreground hover:bg-accent hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        onClick={() => window.history.back()}
        aria-label="Close detail"
      >
        ✕
      </button>

      <header className="mb-6">
        <div className="flex items-center gap-2 mb-2">
          <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-[11px] font-medium ${priorityStyles[item.priority]}`}>
            {item.priority}
          </span>
          <Badge variant="outline">{item.attention_type.replace("_", " ")}</Badge>
          {item.urgent && <Badge variant="danger">Urgent</Badge>}
        </div>
        <h1 id="attention-detail-title" className="text-[22px] font-semibold leading-snug">{item.title}</h1>
        <div className="mt-3 flex flex-wrap items-center gap-3 text-[12.5px] text-muted-foreground">
          <span>{item.sender_email}</span>
          <Separator orientation="vertical" className="h-4" />
          <span>{formatTimeInWarsaw(item.received_at)}</span>
          {item.confidence !== undefined && (
            <>
              <Separator orientation="vertical" className="h-4" />
              <span>Confidence {Math.round(item.confidence * 100)}%</span>
            </>
          )}
          {item.deadline && (
            <>
              <Separator orientation="vertical" className="h-4" />
              <span className="text-amber-600 dark:text-amber-400">Deadline {formatDeadline(item.deadline)}</span>
            </>
          )}
        </div>
        <div className="mt-2 flex items-center gap-2">
          <Badge variant={item.delivery === "delivered" ? "default" : item.delivery === "deferred" ? "secondary" : "outline"}>
            {item.delivery}
          </Badge>
        </div>
      </header>

      <div className="space-y-6">
        {/* Why am I seeing this? */}
        <Card>
          <CardHeader>
            <CardTitle className="text-[14px] flex items-center gap-2">
              <span className="text-muted-foreground">❓</span>
              Why am I seeing this?
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            {explanation.priority_reasons && explanation.priority_reasons.length > 0 && (
              <section>
                <h3 className="text-[12px] font-medium uppercase tracking-[0.08em] text-subtle-foreground mb-2">
                  Priority & Classification Reasons
                </h3>
                <ReasonList reasons={explanation.priority_reasons} />
              </section>
            )}
            {explanation.delivery_reasons && explanation.delivery_reasons.length > 0 && (
              <section>
                <h3 className="text-[12px] font-medium uppercase tracking-[0.08em] text-subtle-foreground mb-2">
                  Delivery & Focus Reasons
                </h3>
                <ReasonList reasons={explanation.delivery_reasons} />
              </section>
            )}
            {!explanation.priority_reasons?.length && !explanation.delivery_reasons?.length && (
              <p className="text-[13px] text-muted-foreground">No stored reasons available for this item.</p>
            )}
            {explanation.sources && explanation.sources.length > 0 && (
              <section>
                <h3 className="text-[12px] font-medium uppercase tracking-[0.08em] text-subtle-foreground mb-2">
                  Sources
                </h3>
                <SourceList sources={explanation.sources} />
              </section>
            )}
          </CardContent>
        </Card>

        {/* Action buttons placeholder */}
        <div className="flex flex-wrap gap-3">
          <button
            type="button"
            className="rounded-control bg-primary px-4 py-2 text-[13.5px] font-medium text-primary-foreground transition-colors hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
            disabled
          >
            Act on this
          </button>
          <button
            type="button"
            className="rounded-control border border-border-strong px-4 py-2 text-[13.5px] font-medium text-foreground transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            disabled
          >
            Dismiss
          </button>
        </div>

        <p className="text-center text-[12px] text-subtle-foreground">
          Data from canonical fixtures: attention_finance_decision.json
        </p>
      </div>
    </div>
  );
}

function ReasonList({ reasons }: { reasons: Reason[] }) {
  return (
    <ul className="space-y-2 pl-4 list-disc">
      {reasons.map((reason, idx) => (
        <li key={`${reason.code}-${idx}`} className="text-[13px] leading-relaxed">
          <span className="font-medium text-foreground">{reason.text}</span>
          <span className="ml-2 text-[11.5px] text-muted-foreground">({reason.origin})</span>
          {reason.source_ids && reason.source_ids.length > 0 && (
            <span className="ml-2 text-[11.5px] text-muted-foreground">Sources: {reason.source_ids.join(", ")}</span>
          )}
        </li>
      ))}
    </ul>
  );
}

function SourceList({ sources }: { sources: SourceRef[] }) {
  return (
    <ul className="space-y-1 pl-4 list-disc text-[12.5px] text-muted-foreground">
      {sources.map((src) => (
        <li key={src.id}>{src.title} ({src.kind.replace("_", " ")})</li>
      ))}
    </ul>
  );
}

function AttentionRow({ item, onClick }: { item: AttentionItem; onClick: () => void }) {
  const priorityStyles: Record<AttentionItem["priority"], string> = {
    high: "bg-danger/10 text-danger border-danger/30",
    medium: "bg-primary/10 text-primary border-primary/30",
    low: "bg-muted text-muted-foreground border-border/60",
  };

  return (
    <li className="group" onClick={onClick}>
      <button
        type="button"
        className="w-full text-left flex gap-3 py-3 pr-4 hover:bg-accent/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
        aria-label={`Open ${item.title}`}
      >
        <span className={`mt-0.5 grid size-6 shrink-0 place-items-center rounded-md ${priorityStyles[item.priority]}`}>
          {item.attention_type === "decision_required" ? "💰" : item.attention_type === "action_required" ? "⚡" : item.attention_type === "urgent" ? "🚨" : "ℹ️"}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <p className="truncate text-[13.5px] font-medium leading-snug group-hover:underline">{item.title}</p>
            {item.priority === "high" && <Badge variant="danger">High</Badge>}
            {item.urgent && <Badge variant="danger">Urgent</Badge>}
          </div>
          <p className="mt-0.5 text-[12px] text-muted-foreground flex flex-wrap gap-3">
            <span>{item.sender_email}</span>
            <span>·</span>
            <span>{formatTimeInWarsaw(item.received_at)}</span>
            <span>·</span>
            <span>{item.attention_type.replace("_", " ")}</span>
          </p>
          {item.deadline && (
            <p className="mt-1 text-[12px] text-amber-600 dark:text-amber-400">
              Deadline {formatDeadline(item.deadline)}
            </p>
          )}
        </div>
        <span className="shrink-0 text-muted-foreground">→</span>
      </button>
      <div className="h-px bg-border/60" />
    </li>
  );
}

function AttentionError({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="mx-auto w-full max-w-md px-6 py-24 text-center">
      <h2 className="text-[17px] font-semibold tracking-tight">Attention unavailable</h2>
      <p className="mt-2 text-[13.5px] leading-relaxed text-muted-foreground">
        The attention queue could not be loaded. Nothing was faked in its place — retry when
        the connection to the EVA backend is available.
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

function AttentionLoading() {
  return (
    <div className="mx-auto w-full max-w-3xl px-6 py-8" aria-busy="true" aria-live="polite">
      <div className="h-4 w-48 animate-pulse rounded-full bg-muted" />
      <div className="mt-6 space-y-3">
        {[...Array(5)].map((_, i) => (
          <div key={i} className="h-14 animate-pulse rounded-card bg-muted" />
        ))}
      </div>
      <p className="mt-6 text-[12.5px] text-muted-foreground">Loading attention queue…</p>
    </div>
  );
}

function AttentionEmpty() {
  return (
    <div className="mx-auto w-full max-w-md px-6 py-24 text-center">
      <h2 className="text-[17px] font-semibold tracking-tight">Nothing needs attention</h2>
      <p className="mt-2 text-[13.5px] leading-relaxed text-muted-foreground">
        Your attention queue is empty. Press the orb and just ask whenever you need something.
      </p>
      <Badge variant="outline" className="mt-5">
        Empty state
      </Badge>
    </div>
  );
}

export default function Attention() {
  const [reloadKey, setReloadKey] = React.useState(0);
  const [selectedItem, setSelectedItem] = React.useState<AttentionItem | null>(null);
  const client = getEvaClient();
  const attention = useEvaQuery(`attention:${reloadKey}`, (c) => c.getAttention(), client);
  const retry = () => setReloadKey((n) => n + 1);

  if (attention.status === "loading") return <AttentionLoading />;
  if (attention.status === "error") return <AttentionError onRetry={retry} />;
  if (attention.status === "ready" && attention.data.items.length === 0) return <AttentionEmpty />;

  const items = attention.data.items;

  if (selectedItem) {
    const [explanation, setExplanation] = React.useState<AttentionExplanationResponse | null>(null);
    const [expStatus, setExpStatus] = React.useState<"loading" | "ready" | "error">("loading");

    React.useEffect(() => {
      let cancelled = false;
      setExpStatus("loading");
      client
        .getAttentionExplanation(selectedItem.id)
        .then((data) => {
          if (!cancelled) {
            setExplanation(data);
            setExpStatus("ready");
          }
        })
        .catch(() => {
          if (!cancelled) setExpStatus("error");
        });
      return () => {
        cancelled = true;
      };
    }, [selectedItem.id, client]);

    if (expStatus === "loading") {
      return (
        <div className="mx-auto w-full max-w-3xl px-6 py-8" aria-busy="true" aria-live="polite">
          <div className="h-4 w-64 animate-pulse rounded-full bg-muted" />
          <p className="mt-6 text-[12.5px] text-muted-foreground">Loading explanation…</p>
        </div>
      );
    }

    if (expStatus === "error" || !explanation) {
      return (
        <div className="mx-auto w-full max-w-3xl px-6 py-8 text-center">
          <h2 className="text-[17px] font-semibold tracking-tight">Explanation unavailable</h2>
          <button
            type="button"
            onClick={() => setSelectedItem(null)}
            className="mt-4 rounded-control border border-border-strong px-4 py-2 text-[13.5px] font-medium text-foreground transition-colors hover:bg-accent"
          >
            Back to queue
          </button>
        </div>
      );
    }

    return <AttentionDetail item={selectedItem} explanation={explanation} />;
  }

  return (
    <div className="mx-auto w-full max-w-3xl px-6 py-8 lg:px-8 lg:py-10">
      <header className="mb-6">
        <h1 className="text-[26px] font-semibold leading-tight tracking-[-0.02em]">Attention</h1>
        <p className="mt-1.5 text-[13.5px] leading-relaxed text-muted-foreground">
          One quiet queue for everything that needs your attention. Tap an item to see why it was delivered.
        </p>
      </header>

      <ul className="divide-y divide-border/60 border-y border-border/60 rounded-card overflow-hidden">
        {items.map((item) => (
          <AttentionRow key={item.id} item={item} onClick={() => setSelectedItem(item)} />
        ))}
      </ul>

      <p className="mt-6 text-center text-[12px] text-subtle-foreground">
        Data from canonical fixture: attention_finance_decision.json
      </p>
    </div>
  );
}