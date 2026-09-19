import * as React from "react";
import { Bell, CircleDollarSign } from "lucide-react";
import { Badge } from "../components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "../components/ui/card";
import { Separator } from "../components/ui/separator";
import { getEvaClient } from "../api/client";
import type {
  AttentionItem,
  Decision,
  FocusCurrentResponse,
  Meeting,
  TodayCalendarResponse,
} from "../api/types.generated";
import { useEvaQuery } from "../hooks/useEvaQuery";
import { FocusPanel } from "../components/focus/FocusPanel";
import {
  compareSpanTimes,
  formatDay,
  formatDeadline,
  formatMoney,
  formatSpanEnd,
  formatSpanStart,
  formatTimeInWarsaw,
  warsawHour,
} from "../lib/format";

/*
 * Today — typed mock foundation (B02A contract handoff).
 *
 * All domain content flows through the EvaClient boundary using canonical
 * A00 generated contracts and canonical fixtures (mock transport). No local
 * domain models and no backend-owned policy/risk/approval logic live here;
 * values rendered are exactly what the contract delivers.
 */

function greetingFor(hour: number): string {
  return hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
}

function MeetingRow({ meeting }: { meeting: Meeting }) {
  const isAllDay = meeting.span.kind === "all_day";
  const start = formatSpanStart(meeting.span);
  const end = isAllDay ? null : formatSpanEnd(meeting.span);
  return (
    <li className="relative flex gap-4 py-3.5 pl-4">
      <span
        aria-hidden
        className={
          meeting.priority === "high"
            ? "absolute left-0 top-1/2 size-1.5 -translate-y-1/2 rounded-full bg-danger"
            : meeting.priority === "medium"
              ? "absolute left-0 top-1/2 size-1.5 -translate-y-1/2 rounded-full bg-primary"
              : "absolute left-0 top-1/2 size-1.5 -translate-y-1/2 rounded-full bg-border-strong"
        }
      />
      <time className="w-14 shrink-0 pt-0.5 font-mono text-[12px] tabular-nums text-muted-foreground">
        {start}
      </time>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <p className="truncate text-[14px] font-medium">{meeting.title}</p>
          {meeting.priority === "high" && <Badge variant="danger">High</Badge>}
          {meeting.priority === "medium" && <Badge variant="secondary">Medium</Badge>}
        </div>
        {(() => {
          const secondary: string[] = [];
          if (!isAllDay && end) secondary.push(`${start}–${end}`);
          if (meeting.location) secondary.push(meeting.location);
          return secondary.length > 0 ? (
            <p className="mt-0.5 text-[12.5px] text-muted-foreground">{secondary.join(" · ")}</p>
          ) : null;
        })()}
      </div>
    </li>
  );
}

function TimelineNowMarker() {
  return (
    <li aria-label="Preview marker" className="relative py-0">
      <div className="absolute inset-x-0 top-1/2 flex items-center gap-3">
        <span className="h-px flex-1 bg-primary/40" />
        <span className="rounded-full bg-primary/10 px-2 py-0.5 font-mono text-[10.5px] tabular-nums text-primary">
          Preview
        </span>
        <span className="h-px flex-1 bg-primary/40" />
      </div>
    </li>
  );
}

function AttentionRow({ item }: { item: AttentionItem }) {
  return (
    <li className="flex gap-3 py-2.5">
      <span className="mt-0.5 grid size-6 shrink-0 place-items-center rounded-md bg-muted text-muted-foreground">
        <Bell className="size-3.5" strokeWidth={1.75} aria-hidden />
      </span>
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <p className="truncate text-[13.5px] font-medium leading-snug">{item.title}</p>
          {item.priority === "high" && <Badge variant="danger">High</Badge>}
        </div>
        <p className="mt-0.5 text-[12px] text-muted-foreground">
          {formatTimeInWarsaw(item.received_at)} · {item.attention_type.replace("_", " ")}
        </p>
      </div>
    </li>
  );
}

function DecisionRow({ decision }: { decision: Decision }) {
  return (
    <li className="flex gap-3 py-2.5">
      <span className="mt-0.5 grid size-6 shrink-0 place-items-center rounded-md bg-muted text-muted-foreground">
        <CircleDollarSign className="size-3.5" strokeWidth={1.75} aria-hidden />
      </span>
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <p className="truncate text-[13.5px] font-medium leading-snug">{decision.title}</p>
          {decision.risk === "high" && <Badge variant="danger">High</Badge>}
        </div>
        <p className="mt-0.5 text-[12px] text-muted-foreground">
          {decision.money ? `${formatMoney(decision.money)} · ` : ""}
          {decision.deadline ? `Deadline ${formatDeadline(decision.deadline)}` : decision.status}
        </p>
      </div>
    </li>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline gap-2">
      <span className="text-[12px] uppercase tracking-[0.06em] text-subtle-foreground">
        {label}
      </span>
      <span className="text-[13.5px] font-medium text-foreground">{value}</span>
    </div>
  );
}

function TodayError({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="mx-auto w-full max-w-md px-6 py-24 text-center">
      <h2 className="text-[17px] font-semibold tracking-tight">Today is unavailable</h2>
      <p className="mt-2 text-[13.5px] leading-relaxed text-muted-foreground">
        Your day could not be loaded. Nothing was faked in its place — retry when
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

function TodayLoading() {
  return (
    <div className="mx-auto w-full max-w-5xl px-5 py-8 lg:px-8 lg:py-10" aria-busy="true" aria-live="polite">
      <div className="h-4 w-40 animate-pulse rounded-full bg-muted" />
      <div className="mt-3 h-8 w-64 animate-pulse rounded-full bg-muted" />
      <div className="mt-9 grid gap-6 lg:grid-cols-5">
        <div className="space-y-3 lg:col-span-3">
          <div className="h-3 w-24 animate-pulse rounded-full bg-muted" />
          <div className="h-14 animate-pulse rounded-card bg-muted" />
          <div className="h-14 animate-pulse rounded-card bg-muted" />
        </div>
        <div className="space-y-3 lg:col-span-2">
          <div className="h-28 animate-pulse rounded-card bg-muted" />
          <div className="h-28 animate-pulse rounded-card bg-muted" />
        </div>
      </div>
      <p className="mt-6 text-[12.5px] text-muted-foreground">Loading your day…</p>
    </div>
  );
}

function TodayEmpty() {
  return (
    <div className="mx-auto w-full max-w-md px-6 py-24 text-center">
      <h2 className="text-[17px] font-semibold tracking-tight">Nothing on your plate</h2>
      <p className="mt-2 text-[13.5px] leading-relaxed text-muted-foreground">
        No meetings, attention items or decisions for today. Press the orb and
        just ask whenever you need something.
      </p>
      <Badge variant="outline" className="mt-5">
        Empty state
      </Badge>
    </div>
  );
}

function TimelineSection({ today }: { today: TodayCalendarResponse }) {
  const meetings = [...today.meetings].sort((a, b) =>
    compareSpanTimes(a.span, b.span)
  );

  return (
    <section aria-labelledby="today-timeline" className="lg:col-span-3">
      <div className="flex items-baseline justify-between">
        <h3
          id="today-timeline"
          className="text-[12px] font-medium uppercase tracking-[0.08em] text-subtle-foreground"
        >
          Timeline
        </h3>
        <span className="text-[12px] text-subtle-foreground">
          {meetings.length} {meetings.length === 1 ? "event" : "events"} ·{" "}
          {today.retrieval_status}
        </span>
      </div>

      <ol className="mt-3 divide-y divide-border/60 border-y border-border/60">
        {meetings.map((meeting) => (
          <MeetingRow key={`${meeting.ref.calendar_id}:${meeting.ref.event_id}`} meeting={meeting} />
        ))}
        {meetings.length > 0 && <TimelineNowMarker />}
      </ol>
      {today.retrieval_status !== "complete" && (
        <p className="mt-3 text-[12px] text-subtle-foreground">
          {today.retrieval_notes?.join(" ") ?? `Retrieval ${today.retrieval_status}.`}
        </p>
      )}
    </section>
  );
}

function AttentionSection({ query }: { query: { status: string; data?: { items: AttentionItem[] }; error?: Error } }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Attention</CardTitle>
        {query.status === "ready" && query.data!.items.length > 0 && (
          <Badge variant="secondary">{query.data!.items.length}</Badge>
        )}
      </CardHeader>
      <CardContent>
        {query.status === "loading" && (
          <p className="py-2 text-[13px] text-muted-foreground">Loading attention…</p>
        )}
        {query.status === "error" && (
          <p className="py-2 text-[13px] text-danger" role="alert">
            Attention could not be loaded: {query.error?.message}
          </p>
        )}
        {query.status === "ready" && query.data!.items.length === 0 && (
          <p className="py-2 text-[13px] text-muted-foreground">Nothing needs attention.</p>
        )}
        {query.status === "ready" && query.data!.items.length > 0 && (
          <ul className="divide-y divide-border/60">
            {query.data!.items.map((item) => (
              <AttentionRow key={item.id} item={item} />
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function DecisionSection({ query }: { query: { status: string; data?: { items: Decision[] }; error?: Error } }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Decisions</CardTitle>
        {query.status === "ready" && query.data!.items.length > 0 && (
          <Badge variant="warning">{query.data!.items.length}</Badge>
        )}
      </CardHeader>
      <CardContent>
        {query.status === "loading" && (
          <p className="py-2 text-[13px] text-muted-foreground">Loading decisions…</p>
        )}
        {query.status === "error" && (
          <p className="py-2 text-[13px] text-danger" role="alert">
            Decisions could not be loaded: {query.error?.message}
          </p>
        )}
        {query.status === "ready" && query.data!.items.length === 0 && (
          <p className="py-2 text-[13px] text-muted-foreground">No decisions to record.</p>
        )}
        {query.status === "ready" && query.data!.items.length > 0 && (
          <>
            <ul className="divide-y divide-border/60">
              {query.data!.items.map((decision) => (
                <DecisionRow key={decision.id} decision={decision} />
              ))}
            </ul>
            <p className="mt-3 text-[11.5px] leading-relaxed text-subtle-foreground">
              Preview only — outcome recording arrives in B02B.
            </p>
          </>
        )}
      </CardContent>
    </Card>
  );
}

export default function Today() {
  const [reloadKey, setReloadKey] = React.useState(0);
  const client = getEvaClient();
  const today = useEvaQuery(`today:${reloadKey}`, (c) => c.getTodayCalendar(), client);
  const attention = useEvaQuery(`attention:${reloadKey}`, (c) => c.getAttention(), client);
  const decisions = useEvaQuery(`decisions:${reloadKey}`, (c) => c.getDecisions(), client);
  const focus = useEvaQuery(`focus:${reloadKey}`, (c) => c.getCurrentFocus(), client);
  const retry = () => setReloadKey((n) => n + 1);

  if (today.status === "loading") return <TodayLoading />;
  if (today.status === "error") {
    return <TodayError onRetry={retry} />;
  }

  const todayData = today.data;
  const sortedMeetings = [...todayData.meetings].sort((a, b) =>
    compareSpanTimes(a.span, b.span)
  );
  const nextUp =
    sortedMeetings.length > 0
      ? `${sortedMeetings[0].title} · ${formatSpanStart(sortedMeetings[0].span)}`
      : "Nothing scheduled";

  const attentionReady = attention.status === "ready";
  const decisionsReady = decisions.status === "ready";
  const attentionItems = attentionReady ? attention.data.items : [];
  const decisionItems = decisionsReady ? decisions.data.items : [];

  // Full-page empty only when all three primary reads completed successfully and are genuinely empty.
  if (
    attentionReady &&
    decisionsReady &&
    todayData.meetings.length === 0 &&
    attentionItems.length === 0 &&
    decisionItems.length === 0
  ) {
    return <TodayEmpty />;
  }

  const hasPartialFailure =
    attention.status === "error" || decisions.status === "error" || focus.status === "error";

  const focusStat = (() => {
    if (focus.status === "loading") return "…";
    if (focus.status === "error") return "Unavailable";
    const session = (focus.data as FocusCurrentResponse).session;
    if (!session) return "Off";
    return `${session.threshold} until ${formatTimeInWarsaw(session.ends_at)}`;
  })();

  return (
    <div className="mx-auto w-full max-w-5xl px-5 py-8 lg:px-8 lg:py-10">
      {/* Greeting */}
      <header>
        <p className="text-[13px] font-medium tracking-wide text-muted-foreground">
          {formatDay(todayData.day)} · {todayData.timezone}
        </p>
        <h2 className="mt-1.5 text-[26px] font-semibold leading-tight tracking-[-0.02em]">
          {greetingFor(warsawHour(new Date()))}
        </h2>
        <p className="mt-3 max-w-md text-[13.5px] leading-relaxed text-muted-foreground">
          Press the orb and just ask — briefings, agenda changes and follow-ups
          start with your voice.
        </p>
      </header>

      {/* Status strip — quiet inline stats, not cards */}
      <div className="mt-6 flex flex-wrap items-center gap-x-5 gap-y-3">
        <Stat label="Next up" value={nextUp} />
        <Separator orientation="vertical" className="h-4" />
        <Stat label="Focus" value={focusStat} />
        <Separator orientation="vertical" className="h-4" />
        <Stat
          label="Attention"
          value={attentionReady ? `${attentionItems.length} new` : "…"}
        />
        <Separator orientation="vertical" className="h-4" />
        <Stat
          label="Decisions"
          value={decisionsReady ? `${decisionItems.length} pending` : "…"}
        />
      </div>

      {/* Main grid */}
      <div className="mt-9 grid gap-6 lg:grid-cols-5">
        <TimelineSection today={todayData} />

        {/* Right rail */}
        <div className="space-y-6 lg:col-span-2">
          <FocusPanel />
          <AttentionSection query={attention} />
          <DecisionSection query={decisions} />
        </div>
      </div>

      {hasPartialFailure && (
        <div role="alert" className="mt-6 flex flex-wrap items-center gap-3 text-[12.5px] text-danger">
          <span>
            Some panels could not be loaded
            {attention.status === "error" ? ` — attention: ${attention.error?.message}` : ""}
            {decisions.status === "error" ? ` — decisions: ${decisions.error?.message}` : ""}
            {focus.status === "error" ? ` — focus: ${focus.error?.message}` : ""}
            .
          </span>
          <button
            type="button"
            onClick={retry}
            className="rounded-control border border-border-strong px-3 py-1 text-[12px] font-medium text-foreground transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            Retry
          </button>
        </div>
      )}
    </div>
  );
}
