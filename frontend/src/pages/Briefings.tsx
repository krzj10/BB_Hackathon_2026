import * as React from "react";
import { Badge } from "../components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "../components/ui/card";
import { Separator } from "../components/ui/separator";
import { getEvaClient } from "../api/client";
import type { Claim, SourceRef, BriefingRequest } from "../api/types.generated";
import { useEvaQuery } from "../hooks/useEvaQuery";
import { formatDay, formatTimeInWarsaw } from "../lib/format";

function ClaimItem({ claim, sources }: { claim: Claim; sources: SourceRef[] }) {
  const sourceList = (claim.source_ids ?? []).map((id) => sources.find((s) => s.id === id)).filter((s): s is SourceRef => s !== undefined);
  const kindStyles: Record<Claim["kind"], string> = {
    fact: "bg-green-50 dark:bg-green-950/30 text-green-800 dark:text-green-200 border-green-200 dark:border-green-800",
    inference: "bg-amber-50 dark:bg-amber-950/30 text-amber-800 dark:text-amber-200 border-amber-200 dark:border-amber-800",
    suggestion: "bg-blue-50 dark:bg-blue-950/30 text-blue-800 dark:text-blue-200 border-blue-200 dark:border-blue-800",
  };
  const kindLabels: Record<Claim["kind"], string> = {
    fact: "Fact",
    inference: "Inference",
    suggestion: "Suggestion",
  };

  return (
    <li className="relative py-3 pl-3">
      <span className="absolute left-0 top-3 size-1.5 rounded-full bg-primary" aria-hidden />
      <div className="flex flex-wrap items-start gap-2">
        <Badge variant="outline" className={kindStyles[claim.kind]}>
          {kindLabels[claim.kind]}
        </Badge>
        <p className="flex-1 text-[13.5px] leading-relaxed">{claim.text}</p>
      </div>
      {sourceList.length > 0 && (
        <details className="mt-2 ml-5 text-[12px] text-muted-foreground">
          <summary className="cursor-pointer select-none">Sources ({sourceList.length})</summary>
          <ul className="mt-1 space-y-1 pl-4 list-disc">
            {sourceList.map((src) => (
              <li key={src.id} className="flex items-center gap-2">
                <span className="text-[11.5px]">{src.title}</span>
                {src.url && (
                  <a href={src.url} target="_blank" rel="noopener noreferrer" className="text-primary underline-offset-2 hover:underline">
                    open
                  </a>
                )}
              </li>
            ))}
          </ul>
        </details>
      )}
    </li>
  );
}

function ClaimList({ title, claims, sources, icon }: { title: string; claims: Claim[] | undefined; sources: SourceRef[]; icon: React.ReactNode }) {
  if (!claims || claims.length === 0) return null;

  return (
    <Card className="overflow-hidden">
      <CardHeader className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          {icon}
          <CardTitle className="text-[14px]">{title}</CardTitle>
        </div>
        <Badge variant="secondary">{claims.length}</Badge>
      </CardHeader>
      <CardContent className="p-0">
        <ul className="divide-y divide-border/60">
          {claims.map((claim, idx) => (
            <ClaimItem key={`${claim.kind}-${idx}`} claim={claim} sources={sources} />
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}

function SourceList({ sources }: { sources: SourceRef[] }) {
  if (!sources || sources.length === 0) return null;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-[14px] flex items-center gap-2">
          <span className="text-muted-foreground">📎</span>
          Sources & Evidence
        </CardTitle>
        <Badge variant="outline">{sources.length}</Badge>
      </CardHeader>
      <CardContent>
        <ul className="space-y-2">
          {sources.map((src) => (
            <li key={src.id} className="flex items-start gap-3 p-2 rounded-lg border border-border/60 hover:bg-accent transition-colors">
              <span className="mt-0.5 text-muted-foreground">📄</span>
              <div className="min-w-0 flex-1">
                <p className="text-[13px] font-medium">{src.title}</p>
                <p className="mt-0.5 text-[12px] text-muted-foreground">
                  {src.kind.replace("_", " ")} · {formatTimeInWarsaw(src.retrieved_at)}
                </p>
                {src.url && (
                  <a href={src.url} target="_blank" rel="noopener noreferrer" className="mt-1 inline-block text-[12px] text-primary underline-offset-2 hover:underline">
                    open
                  </a>
                )}
              </div>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}

function BriefingError({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="mx-auto w-full max-w-3xl px-6 py-24 text-center">
      <h2 className="text-[17px] font-semibold tracking-tight">Briefing unavailable</h2>
      <p className="mt-2 text-[13.5px] leading-relaxed text-muted-foreground">
        The executive briefing could not be loaded. Nothing was faked in its place — retry when
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

function BriefingLoading() {
  return (
    <div className="mx-auto w-full max-w-3xl px-6 py-8" aria-busy="true" aria-live="polite">
      <div className="h-4 w-64 animate-pulse rounded-full bg-muted" />
      <div className="mt-3 h-8 w-80 animate-pulse rounded-full bg-muted" />
      <div className="mt-6 space-y-4">
        {[...Array(5)].map((_, i) => (
          <div key={i} className="h-24 animate-pulse rounded-card bg-muted" />
        ))}
      </div>
      <p className="mt-6 text-[12.5px] text-muted-foreground">Loading briefing…</p>
    </div>
  );
}

export default function Briefings() {
  const [reloadKey, setReloadKey] = React.useState(0);
  const client = getEvaClient();
  const briefingRequest: BriefingRequest = { meeting_ref: { calendar_id: "primary", event_id: "evt-demo-acme-contract-review-001" }, language: "pl" };
  const briefing = useEvaQuery(`briefing:${reloadKey}`, (c) => c.getBriefing(briefingRequest), client);
  const retry = () => setReloadKey((n) => n + 1);

  if (briefing.status === "loading") return <BriefingLoading />;
  if (briefing.status === "error") return <BriefingError onRetry={retry} />;

  const data = briefing.data.briefing;
  const sources = data.sources ?? [];

  return (
    <div className="mx-auto w-full max-w-3xl px-6 py-8 lg:px-8 lg:py-10">
      {/* Header */}
      <header className="mb-8">
        <p className="text-[13px] font-medium tracking-wide text-muted-foreground">
          {formatDay(data.meeting.span.kind === "timed" ? data.meeting.span.start.split("T")[0] : data.meeting.span.start_date)} · {data.meeting.span.kind === "timed" ? data.meeting.span.timezone : "—"}
        </p>
        <h1 className="mt-1.5 text-[26px] font-semibold leading-tight tracking-[-0.02em]">{data.meeting.title}</h1>
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <Badge variant="outline">Language: {data.language.toUpperCase()}</Badge>
          <Badge variant={data.retrieval_status === "complete" ? "default" : "secondary"}>{data.retrieval_status}</Badge>
          {data.meeting.priority === "high" && <Badge variant="danger">High priority</Badge>}
          {data.meeting.priority === "medium" && <Badge variant="secondary">Medium priority</Badge>}
        </div>
        {data.retrieval_status !== "complete" && data.retrieval_notes && data.retrieval_notes.length > 0 && (
          <p className="mt-3 text-[12.5px] text-subtle-foreground">
            {data.retrieval_notes.join(" ")}
          </p>
        )}
        <div className="mt-4 flex items-center gap-3 text-[13px] text-muted-foreground">
          <span className="font-mono tabular-nums">
            {data.meeting.span.kind === "timed" ? `${formatTimeInWarsaw(data.meeting.span.start)}–${formatTimeInWarsaw(data.meeting.span.end)}` : "All day"}
          </span>
          <Separator orientation="vertical" className="h-4" />
          <span>Generated {formatTimeInWarsaw(data.generated_at)}</span>
        </div>
      </header>

      {/* Spoken Summary */}
      <Card className="mb-6">
        <CardHeader>
          <CardTitle className="text-[14px] flex items-center gap-2">
            <span className="text-muted-foreground">🎙️</span>
            Spoken Summary
          </CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-[14px] leading-relaxed italic text-muted-foreground">{data.spoken_summary || "—"}</p>
        </CardContent>
      </Card>

      {/* Content Grid */}
      <div className="space-y-6">
        <ClaimList title="Previous Interactions" claims={data.previous_interactions} sources={sources} icon={<span className="text-muted-foreground">💬</span>} />
        <ClaimList title="Open Topics" claims={data.open_topics} sources={sources} icon={<span className="text-muted-foreground">❓</span>} />
        <ClaimList title="Previous Decisions" claims={data.previous_decisions} sources={sources} icon={<span className="text-muted-foreground">✅</span>} />
        <ClaimList title="Risks" claims={data.risks} sources={sources} icon={<span className="text-muted-foreground">⚠️</span>} />
        <ClaimList title="Suggestions" claims={data.suggestions} sources={sources} icon={<span className="text-muted-foreground">💡</span>} />
        <SourceList sources={sources} />
      </div>

      <p className="mt-8 text-center text-[12px] text-subtle-foreground">
        Data from canonical fixture: briefing_acme_pl.json
      </p>
    </div>
  );
}