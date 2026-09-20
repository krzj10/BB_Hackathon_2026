import * as React from "react";
import { Badge } from "../components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "../components/ui/card";
import { Separator } from "../components/ui/separator";
import { getEvaClient } from "../api/client";
import type {
  BriefingRequest,
  Claim,
  Meeting,
  MeetingPriority,
  MeetingRef,
  SourceRef,
} from "../api/types.generated";
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
        <p className="min-w-0 flex-1 text-[13.5px] leading-relaxed break-words [overflow-wrap:anywhere]">{claim.text}</p>
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
          Sources &amp; Evidence
        </CardTitle>
        <Badge variant="outline">{sources.length}</Badge>
      </CardHeader>
      <CardContent>
        <ul className="space-y-2">
          {sources.map((src) => (
            <li key={src.id} className="flex items-start gap-3 p-2 rounded-lg border border-border/60 hover:bg-accent transition-colors">
              <span className="mt-0.5 text-muted-foreground">📄</span>
              <div className="min-w-0 flex-1">
                <p className="text-[13px] font-medium break-words [overflow-wrap:anywhere]">{src.title}</p>
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
    <div className="w-full px-2 py-16 text-center">
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
    <div className="w-full px-2 py-8" aria-busy="true" aria-live="polite">
      <div className="h-4 w-64 max-w-full animate-pulse rounded-full bg-muted" />
      <div className="mt-3 h-8 w-80 max-w-full animate-pulse rounded-full bg-muted" />
      <div className="mt-6 space-y-4">
        {[...Array(5)].map((_, i) => (
          <div key={i} className="h-24 animate-pulse rounded-card bg-muted" />
        ))}
      </div>
      <p className="mt-6 text-[12.5px] text-muted-foreground">Loading briefing…</p>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Briefing library                                                            */
/* -------------------------------------------------------------------------- */

type BriefingAnchor = {
  key: string;
  label: string;
  hint: string;
  scenario?: "monday" | "pto";
  ref: MeetingRef;
  timeLabel: string;
  priority: MeetingPriority;
  conflicting: boolean;
};

function startMs(meeting: Meeting): number {
  const raw = meeting.span.kind === "timed" ? meeting.span.start : meeting.span.start_date;
  const ms = Date.parse(raw);
  return Number.isFinite(ms) ? ms : Number.MAX_SAFE_INTEGER;
}

function endMs(meeting: Meeting): number {
  if (meeting.span.kind !== "timed") return startMs(meeting) + 24 * 60 * 60 * 1000;
  const ms = Date.parse(meeting.span.end);
  return Number.isFinite(ms) ? ms : startMs(meeting);
}

function timeLabelFor(meeting: Meeting): string {
  if (meeting.span.kind !== "timed") return "All day";
  return `${formatTimeInWarsaw(meeting.span.start)}–${formatTimeInWarsaw(meeting.span.end)}`;
}

/** A meeting overlaps another one when the half-open intervals intersect. */
function isConflicting(meeting: Meeting, others: Meeting[]): boolean {
  return others.some(
    (other) =>
      other.ref.event_id !== meeting.ref.event_id &&
      startMs(other) < endMs(meeting) &&
      startMs(meeting) < endMs(other),
  );
}

/**
 * Briefing library: two scenario presets on top of one briefing per meeting.
 * Presets only choose which anchor the briefing is built for — every claim
 * still comes from the real briefing service, nothing is canned here.
 */
export function buildBriefingAnchors(meetings: Meeting[]): BriefingAnchor[] {
  const sorted = [...meetings].sort((a, b) => startMs(a) - startMs(b));
  if (sorted.length === 0) return [];

  const anchorOf = (meeting: Meeting): BriefingAnchor => ({
    key: `meeting:${meeting.ref.calendar_id}:${meeting.ref.event_id}`,
    label: meeting.title,
    hint: "Meeting briefing",
    ref: meeting.ref,
    timeLabel: timeLabelFor(meeting),
    priority: meeting.priority,
    conflicting: isConflicting(meeting, sorted),
  });

  const first = sorted[0];
  const upcoming = sorted.find((meeting) => endMs(meeting) > Date.now());
  const scenarios: BriefingAnchor[] = [
    {
      ...anchorOf(first),
      key: "scenario:monday",
      label: "Monday Briefing",
      hint: "Start of the week — first block on the calendar",
      scenario: "monday",
    },
    {
      ...anchorOf(upcoming ?? sorted[sorted.length - 1]),
      key: "scenario:after-pto",
      label: "Briefing after PTO",
      hint: "Back from time off — next meeting and what moved while you were away",
      scenario: "pto",
    },
  ];

  return [...scenarios, ...sorted.map(anchorOf)];
}

function BriefingListItem({
  anchor,
  selected,
  onSelect,
}: {
  anchor: BriefingAnchor;
  selected: boolean;
  onSelect: (key: string) => void;
}) {
  return (
    <button
      type="button"
      onClick={() => onSelect(anchor.key)}
      aria-current={selected ? "true" : undefined}
      className={
        selected
          ? "w-full shrink-0 rounded-control border border-primary/40 bg-accent px-3 py-2.5 text-left transition-colors lg:w-full"
          : "w-full shrink-0 rounded-control border border-transparent px-3 py-2.5 text-left transition-colors hover:bg-accent/60 lg:w-full"
      }
    >
      <span className="flex items-center gap-2">
        {anchor.scenario && <span aria-hidden className="text-[13px]">{anchor.scenario === "monday" ? "🗓️" : "🌴"}</span>}
        <span className="min-w-0 flex-1 truncate text-[13.5px] font-medium">{anchor.label}</span>
      </span>
      <span className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11.5px] text-subtle-foreground">
        <span className="font-mono tabular-nums">{anchor.timeLabel}</span>
        {anchor.conflicting && (
          <Badge variant="danger" className="px-1.5 py-0 text-[10px]">
            Conflict
          </Badge>
        )}
        {anchor.scenario && <span className="truncate">{anchor.hint}</span>}
      </span>
    </button>
  );
}

function BriefingDocument({ meetingRef }: { meetingRef: MeetingRef }) {
  const [reloadKey, setReloadKey] = React.useState(0);
  const client = getEvaClient();
  const request: BriefingRequest = { meeting_ref: meetingRef, language: "pl" };
  const briefing = useEvaQuery(
    `briefing:${meetingRef.calendar_id}:${meetingRef.event_id}:${reloadKey}`,
    (c) => c.getBriefing(request),
    client,
  );

  if (briefing.status === "loading") return <BriefingLoading />;
  if (briefing.status === "error") return <BriefingError onRetry={() => setReloadKey((n) => n + 1)} />;

  const data = briefing.data.briefing;
  const sources = data.sources ?? [];

  return (
    <article className="min-w-0">
      <header className="mb-8">
        <p className="text-[13px] font-medium tracking-wide text-muted-foreground">
          {formatDay(data.meeting.span.kind === "timed" ? data.meeting.span.start.split("T")[0] : data.meeting.span.start_date)} ·{" "}
          {data.meeting.span.kind === "timed" ? data.meeting.span.timezone : "—"}
        </p>
        <h1 className="mt-1.5 text-[26px] font-semibold leading-tight tracking-[-0.02em] break-words [overflow-wrap:anywhere]">
          {data.meeting.title}
        </h1>
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <Badge variant="outline">Language: {data.language.toUpperCase()}</Badge>
          <Badge variant={data.retrieval_status === "complete" ? "default" : "secondary"}>{data.retrieval_status}</Badge>
          {data.meeting.priority === "high" && <Badge variant="danger">High priority</Badge>}
          {data.meeting.priority === "medium" && <Badge variant="secondary">Medium priority</Badge>}
        </div>
        {data.retrieval_status !== "complete" && data.retrieval_notes && data.retrieval_notes.length > 0 && (
          <p className="mt-3 text-[12.5px] text-subtle-foreground">{data.retrieval_notes.join(" ")}</p>
        )}
        <div className="mt-4 flex flex-wrap items-center gap-3 text-[13px] text-muted-foreground">
          <span className="font-mono tabular-nums">{timeLabelFor(data.meeting)}</span>
          <Separator orientation="vertical" className="hidden h-4 sm:block" />
          <span>Generated {formatTimeInWarsaw(data.generated_at)}</span>
        </div>
      </header>

      <Card className="mb-6">
        <CardHeader>
          <CardTitle className="text-[14px] flex items-center gap-2">
            <span className="text-muted-foreground">🎙️</span>
            Spoken Summary
          </CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-[14px] leading-relaxed italic text-muted-foreground break-words [overflow-wrap:anywhere]">
            {data.spoken_summary || "—"}
          </p>
        </CardContent>
      </Card>

      <div className="space-y-6 min-w-0">
        <ClaimList title="Previous Interactions" claims={data.previous_interactions} sources={sources} icon={<span className="text-muted-foreground">💬</span>} />
        <ClaimList title="Open Topics" claims={data.open_topics} sources={sources} icon={<span className="text-muted-foreground">❓</span>} />
        <ClaimList title="Previous Decisions" claims={data.previous_decisions} sources={sources} icon={<span className="text-muted-foreground">✅</span>} />
        <ClaimList title="Risks" claims={data.risks} sources={sources} icon={<span className="text-muted-foreground">⚠️</span>} />
        <ClaimList title="Suggestions" claims={data.suggestions} sources={sources} icon={<span className="text-muted-foreground">💡</span>} />
        <SourceList sources={sources} />
      </div>

      <p className="mt-8 text-center text-[12px] text-subtle-foreground">
        Computed by the EVA briefing service from calendar and attention evidence.
      </p>
    </article>
  );
}

export default function Briefings() {
  const client = getEvaClient();
  const calendar = useEvaQuery("calendar:today", (c) => c.getTodayCalendar(), client);
  const anchors = React.useMemo(
    () => (calendar.status === "ready" ? buildBriefingAnchors(calendar.data.meetings) : []),
    [calendar],
  );
  const [selectedKey, setSelectedKey] = React.useState<string | null>(null);
  const selected = anchors.find((anchor) => anchor.key === selectedKey) ?? anchors[0] ?? null;

  if (calendar.status === "error") {
    return (
      <div className="mx-auto w-full max-w-3xl px-6 py-8 lg:px-8">
        <BriefingError onRetry={() => window.location.reload()} />
      </div>
    );
  }

  if (calendar.status === "loading") {
    return (
      <div className="mx-auto w-full max-w-6xl px-6 py-8 lg:px-8">
        <BriefingLoading />
      </div>
    );
  }

  return (
    <div className="mx-auto w-full max-w-6xl px-4 py-8 sm:px-6 lg:px-8">
      <header className="mb-6">
        <h1 className="text-[22px] font-semibold tracking-[-0.02em]">Briefings</h1>
        <p className="mt-1 text-[13.5px] text-muted-foreground">
          Scenario presets and a briefing for every meeting on {calendar.data.day}.
        </p>
      </header>

      {anchors.length === 0 ? (
        <Card>
          <CardContent className="px-4 py-10 text-center">
            <p className="text-[14px] font-medium">No briefings yet</p>
            <p className="mt-1.5 text-[13px] text-muted-foreground">
              Briefings appear once there is a meeting on your calendar for today.
            </p>
          </CardContent>
        </Card>
      ) : (
        <div className="lg:grid lg:grid-cols-[248px_minmax(0,1fr)] lg:items-start lg:gap-8">
          <nav
            aria-label="Briefings"
            className="mb-8 flex gap-2 overflow-x-auto pb-1 lg:sticky lg:top-6 lg:mb-0 lg:block lg:max-h-[calc(100dvh-4rem)] lg:overflow-y-auto lg:overscroll-contain"
          >
            {anchors.map((anchor) => (
              <div key={anchor.key} className="w-64 shrink-0 lg:w-full">
                <BriefingListItem anchor={anchor} selected={selected?.key === anchor.key} onSelect={setSelectedKey} />
              </div>
            ))}
          </nav>

          {selected && <BriefingDocument meetingRef={selected.ref} />}
        </div>
      )}
    </div>
  );
}
