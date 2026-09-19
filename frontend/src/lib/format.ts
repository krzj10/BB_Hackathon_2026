import type { MeetingSpan, Money } from "../api/types.generated";

const timeIn = (timeZone: string) =>
  new Intl.DateTimeFormat("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
    timeZone,
  });

export function formatSpanStart(span: MeetingSpan, fallbackTimeZone = "Europe/Warsaw"): string {
  if (span.kind === "all_day") return "All day";
  return timeIn(span.timezone || fallbackTimeZone).format(new Date(span.start));
}

/** All-day spans have an exclusive end date, not a wall-clock end time — returns null for them. */
export function formatSpanEnd(
  span: MeetingSpan,
  fallbackTimeZone = "Europe/Warsaw"
): string | null {
  if (span.kind === "all_day") return null;
  return timeIn(span.timezone || fallbackTimeZone).format(new Date(span.end));
}

const warsawTime = timeIn("Europe/Warsaw");

export function formatTimeInWarsaw(iso: string): string {
  return warsawTime.format(new Date(iso));
}

const warsawHourFmt = new Intl.DateTimeFormat("en-GB", {
  hour: "numeric",
  hourCycle: "h23",
  timeZone: "Europe/Warsaw",
});

export function warsawHour(date: Date): number {
  return Number(warsawHourFmt.format(date));
}

const dayFmt = new Intl.DateTimeFormat("en-GB", {
  weekday: "long",
  day: "numeric",
  month: "long",
  year: "numeric",
  timeZone: "Europe/Warsaw",
});

export function formatDay(day: string): string {
  return dayFmt.format(new Date(`${day}T12:00:00Z`));
}

const deadlineFmt = new Intl.DateTimeFormat("en-GB", {
  weekday: "short",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
  timeZone: "Europe/Warsaw",
});

export function formatDeadline(iso: string): string {
  return deadlineFmt.format(new Date(iso));
}

export function formatMoney(money: Money): string {
  const major = money.amount_minor_units / 100;
  const grouped = major
    .toLocaleString("en-US", { maximumFractionDigits: 2 })
    .replace(/,/g, " ");
  return `${grouped} ${money.currency}`;
}

export function compareSpanTimes(a: MeetingSpan, b: MeetingSpan): number {
  const keyA = a.kind === "all_day" ? "00:00" : formatSpanStart(a);
  const keyB = b.kind === "all_day" ? "00:00" : formatSpanStart(b);
  return keyA < keyB ? -1 : keyA > keyB ? 1 : 0;
}
