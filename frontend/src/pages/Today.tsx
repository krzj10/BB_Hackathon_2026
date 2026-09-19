import { Bell, CircleDollarSign } from "lucide-react";
import { Badge } from "../components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "../components/ui/card";
import { Separator } from "../components/ui/separator";

/*
 * Today — visual structure only (B02A).
 *
 * All content below is presentation-safe inline placeholder text.
 * No Meeting / Attention / Decision / ProposedAction shapes are
 * defined here; typed data arrives via A00 generated contracts in B02B.
 */

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

const warshawDate = new Intl.DateTimeFormat("en-GB", {
  weekday: "long",
  day: "numeric",
  month: "long",
  timeZone: "Europe/Warsaw",
});

/** Warsaw wall-clock hour, so the greeting matches the displayed date. */
const warshawHour = new Intl.DateTimeFormat("en-GB", {
  hour: "numeric",
  hourCycle: "h23",
  timeZone: "Europe/Warsaw",
});

/** Fixed preview time between the 11:00 and 14:00 placeholder events. */
const PREVIEW_NOW_TIME = "11:40";

export default function Today() {
  const now = new Date();
  const hour = Number(warshawHour.format(now));
  const greeting = hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";

  return (
    <div className="mx-auto w-full max-w-5xl px-5 py-8 lg:px-8 lg:py-10">
      {/* Greeting */}
      <header>
        <p className="text-[13px] font-medium tracking-wide text-muted-foreground">
          {warshawDate.format(now)}
        </p>
        <h2 className="mt-1.5 text-[26px] font-semibold leading-tight tracking-[-0.02em]">
          {greeting}
        </h2>
        <p className="mt-3 max-w-md text-[13.5px] leading-relaxed text-muted-foreground">
          Press the orb and just ask — briefings, agenda changes and follow-ups
          start with your voice.
        </p>
      </header>

      {/* Status strip — quiet inline stats, not cards */}
      <div className="mt-6 flex flex-wrap items-center gap-x-5 gap-y-3">
        <Stat label="Next up" value="ACME Contract Review · 11:00" />
        <Separator orientation="vertical" className="h-4" />
        <Stat label="Focus" value="Off" />
        <Separator orientation="vertical" className="h-4" />
        <Stat label="Attention" value="2 new" />
        <Separator orientation="vertical" className="h-4" />
        <Stat label="Decisions" value="1 pending" />
      </div>

      {/* Main grid */}
      <div className="mt-9 grid gap-6 lg:grid-cols-5">
        {/* Timeline */}
        <section aria-labelledby="today-timeline" className="lg:col-span-3">
          <div className="flex items-baseline justify-between">
            <h3
              id="today-timeline"
              className="text-[12px] font-medium uppercase tracking-[0.08em] text-subtle-foreground"
            >
              Timeline
            </h3>
            <span className="text-[12px] text-subtle-foreground">4 events · preview</span>
          </div>

          <ol className="mt-3 divide-y divide-border/60 border-y border-border/60">
            {/* Past event — muted */}
            <li className="relative flex gap-4 py-3.5 pl-4 opacity-60">
              <span
                aria-hidden
                className="absolute left-0 mt-2 size-1.5 rounded-full bg-border-strong"
              />
              <time className="w-14 shrink-0 pt-0.5 font-mono text-[12px] tabular-nums text-muted-foreground">
                09:30
              </time>
              <div className="min-w-0 flex-1">
                <p className="truncate text-[14px] font-medium">Team Sync</p>
                <p className="mt-0.5 text-[12.5px] text-muted-foreground">
                  Weekly · 4 attendees
                </p>
              </div>
            </li>

            {/* Current priority event */}
            <li className="relative flex gap-4 py-3.5 pl-4">
              <span
                aria-hidden
                className="absolute left-0 top-1/2 size-1.5 -translate-y-1/2 rounded-full bg-primary"
              />
              <time className="w-14 shrink-0 pt-0.5 font-mono text-[12px] tabular-nums text-muted-foreground">
                11:00
              </time>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <p className="truncate text-[14px] font-medium">ACME Contract Review</p>
                  <Badge variant="danger">High</Badge>
                </div>
                <p className="mt-0.5 text-[12.5px] text-muted-foreground">
                  Agenda review · materials attached
                </p>
              </div>
            </li>

            {/* Now marker — fixed preview time consistent with its position */}
            <li aria-label={`Now marker (preview) — ${PREVIEW_NOW_TIME}`} className="relative py-0">
              <div className="absolute inset-x-0 top-1/2 flex items-center gap-3">
                <span className="h-px flex-1 bg-primary/40" />
                <span className="rounded-full bg-primary/10 px-2 py-0.5 font-mono text-[10.5px] tabular-nums text-primary">
                  Now · {PREVIEW_NOW_TIME}
                </span>
                <span className="h-px flex-1 bg-primary/40" />
              </div>
            </li>

            <li className="relative flex gap-4 py-3.5 pl-4">
              <span
                aria-hidden
                className="absolute left-0 mt-2 size-1.5 rounded-full bg-border-strong"
              />
              <time className="w-14 shrink-0 pt-0.5 font-mono text-[12px] tabular-nums text-muted-foreground">
                14:00
              </time>
              <div className="min-w-0 flex-1">
                <p className="truncate text-[14px] font-medium">Catch-up</p>
                <p className="mt-0.5 text-[12.5px] text-muted-foreground">1:1 · optional</p>
              </div>
            </li>

            <li className="relative flex gap-4 py-3.5 pl-4">
              <span
                aria-hidden
                className="absolute left-0 top-1/2 size-1.5 -translate-y-1/2 rounded-full bg-danger"
              />
              <time className="w-14 shrink-0 pt-0.5 font-mono text-[12px] tabular-nums text-muted-foreground">
                16:30
              </time>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <p className="truncate text-[14px] font-medium">Board Call</p>
                  <Badge variant="danger">High</Badge>
                </div>
                <p className="mt-0.5 text-[12.5px] text-muted-foreground">
                  Quarterly review · 6 attendees
                </p>
              </div>
            </li>
          </ol>
        </section>

        {/* Right rail */}
        <div className="space-y-6 lg:col-span-2">
          <Card>
            <CardHeader>
              <CardTitle>Attention</CardTitle>
              <Badge variant="secondary">2</Badge>
            </CardHeader>
            <CardContent>
              <ul className="divide-y divide-border/60">
                <li className="flex gap-3 py-2.5">
                  <span className="mt-0.5 grid size-6 shrink-0 place-items-center rounded-md bg-muted text-muted-foreground">
                    <Bell className="size-3.5" strokeWidth={1.75} aria-hidden />
                  </span>
                  <div className="min-w-0">
                    <p className="text-[13.5px] font-medium leading-snug">
                      CFO request — pricing approval
                    </p>
                    <p className="mt-0.5 text-[12px] text-muted-foreground">
                      09:12 · flagged sender
                    </p>
                  </div>
                </li>
                <li className="flex gap-3 py-2.5">
                  <span className="mt-0.5 grid size-6 shrink-0 place-items-center rounded-md bg-muted text-muted-foreground">
                    <Bell className="size-3.5" strokeWidth={1.75} aria-hidden />
                  </span>
                  <div className="min-w-0">
                    <p className="text-[13.5px] font-medium leading-snug">
                      Migration window unresolved
                    </p>
                    <p className="mt-0.5 text-[12px] text-muted-foreground">
                      Yesterday · thread
                    </p>
                  </div>
                </li>
              </ul>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Decisions</CardTitle>
              <Badge variant="warning">1</Badge>
            </CardHeader>
            <CardContent>
              <div className="flex gap-3 py-1">
                <span className="mt-0.5 grid size-6 shrink-0 place-items-center rounded-md bg-muted text-muted-foreground">
                  <CircleDollarSign className="size-3.5" strokeWidth={1.75} aria-hidden />
                </span>
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <p className="truncate text-[13.5px] font-medium leading-snug">
                      Approve Q3 invoice — 12 400 PLN
                    </p>
                    <Badge variant="danger">High</Badge>
                  </div>
                  <p className="mt-0.5 text-[12px] text-muted-foreground">
                    Deadline Fri 17:00 · evidence attached
                  </p>
                </div>
              </div>
              <p className="mt-3 text-[11.5px] leading-relaxed text-subtle-foreground">
                Preview only — outcome recording arrives in B02B.
              </p>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
