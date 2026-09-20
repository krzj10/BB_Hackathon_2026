import * as React from "react";
import { Library, Search } from "lucide-react";
import { Badge } from "../components/ui/badge";
import { Card, CardContent } from "../components/ui/card";
import { getEvaClient } from "../api/client";
import type { AttentionItem } from "../api/types.generated";
import { useEvaQuery } from "../hooks/useEvaQuery";
import {
  KNOWLEDGE_ENTRIES,
  KNOWLEDGE_KIND_LABELS,
  KNOWLEDGE_KIND_ORDER,
  type KnowledgeEntry,
  type KnowledgeKind,
} from "../lib/knowledge";

/**
 * How many live Attention items an entry is about. Matching is deliberately
 * dumb and inspectable: the entry's name and tags against a thread's subject
 * or sender, so the badge can always be explained.
 */
function relatedThreadCount(entry: KnowledgeEntry, items: AttentionItem[]): number {
  const terms = [entry.name, ...entry.tags]
    .map((term) => term.toLowerCase().replace(/[^a-ząćęłńóśźż0-9]/gi, ""))
    .filter((term) => term.length >= 4);
  if (terms.length === 0) return 0;

  return items.filter((item) => {
    // Same normalization on both sides: "Anna Kowalska" has to match the
    // address "anna.kowalska@acme.example".
    const haystack = `${item.title ?? ""} ${item.sender_email ?? ""}`
      .toLowerCase()
      .replace(/[^a-ząćęłńóśźż0-9]/gi, "");
    return terms.some((term) => haystack.includes(term));
  }).length;
}

function EntryCard({ entry, related }: { entry: KnowledgeEntry; related: number | null }) {
  return (
    <Card className="flex h-full flex-col">
      <CardContent className="flex h-full flex-col p-4">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h3 className="text-[14.5px] font-semibold leading-snug break-words [overflow-wrap:anywhere]">{entry.name}</h3>
            <p className="mt-0.5 text-[12.5px] text-muted-foreground break-words [overflow-wrap:anywhere]">{entry.role}</p>
          </div>
          <Badge variant={entry.confidence === "confirmed" ? "outline" : "secondary"} className="shrink-0">
            {entry.confidence}
          </Badge>
        </div>

        <p className="mt-3 text-[13px] leading-relaxed break-words [overflow-wrap:anywhere]">{entry.summary}</p>

        <ul className="mt-3 space-y-1.5">
          {entry.facts.map((fact) => (
            <li key={fact} className="flex gap-2 text-[12.5px] leading-relaxed text-muted-foreground">
              <span aria-hidden className="mt-[7px] size-1 shrink-0 rounded-full bg-primary/70" />
              <span className="min-w-0 break-words [overflow-wrap:anywhere]">{fact}</span>
            </li>
          ))}
        </ul>

        <div className="mt-auto pt-4">
          <div className="flex flex-wrap items-center gap-1.5">
            {entry.tags.map((tag) => (
              <span key={tag} className="rounded-full border border-border/60 px-2 py-0.5 text-[11px] text-subtle-foreground">
                {tag}
              </span>
            ))}
            {related !== null && related > 0 && (
              <Badge variant="outline" className="text-[11px]">
                {related} w Attention
              </Badge>
            )}
          </div>
          <p className="mt-2 text-[11px] text-subtle-foreground break-words [overflow-wrap:anywhere]">Źródło: {entry.source}</p>
        </div>
      </CardContent>
    </Card>
  );
}

export default function Knowledge() {
  const client = getEvaClient();
  const attention = useEvaQuery("attention", (c) => c.getAttention(), client);
  const [query, setQuery] = React.useState("");
  const [kind, setKind] = React.useState<KnowledgeKind | "all">("all");

  const items = attention.status === "ready" ? attention.data.items ?? [] : null;

  const normalized = query.trim().toLowerCase();
  const matches = (entry: KnowledgeEntry) => {
    if (kind !== "all" && entry.kind !== kind) return false;
    if (!normalized) return true;
    const haystack = [entry.name, entry.role, entry.summary, ...entry.facts, ...entry.tags]
      .join(" ")
      .toLowerCase();
    return haystack.includes(normalized);
  };

  const visible = KNOWLEDGE_ENTRIES.filter(matches);
  const counts = KNOWLEDGE_KIND_ORDER.map((value) => ({
    value,
    label: KNOWLEDGE_KIND_LABELS[value],
    count: KNOWLEDGE_ENTRIES.filter((entry) => entry.kind === value).length,
  }));

  return (
    <div className="mx-auto w-full max-w-5xl px-4 py-8 sm:px-6 lg:px-8">
      <header className="mb-6">
        <div className="flex items-center gap-2.5">
          <Library className="size-5 text-muted-foreground" strokeWidth={1.75} aria-hidden />
          <h1 className="text-[22px] font-semibold tracking-[-0.02em]">Knowledge</h1>
        </div>
        <p className="mt-1.5 max-w-2xl text-[13.5px] leading-relaxed text-muted-foreground">
          Trwałe notatki, na których Eva opiera decyzje: ludzie, konta, projekty i zasady pracy.
          Każda pozycja ma źródło — jeśli źródło znika, notatka nadaje się do sprostowania.
        </p>
      </header>

      <div className="mb-6 space-y-3">
        <div className="relative">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-subtle-foreground" aria-hidden />
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Szukaj w wiedzy Evy…"
            aria-label="Szukaj w wiedzy Evy"
            className="w-full rounded-control border border-border-strong bg-background py-2 pl-9 pr-3 text-[13.5px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          />
        </div>

        <div className="flex flex-wrap items-center gap-1.5">
          <button
            type="button"
            onClick={() => setKind("all")}
            aria-pressed={kind === "all"}
            className={
              kind === "all"
                ? "rounded-full border border-primary/40 bg-accent px-3 py-1 text-[12px] font-medium transition-colors"
                : "rounded-full border border-border/60 px-3 py-1 text-[12px] text-muted-foreground transition-colors hover:bg-accent/60"
            }
          >
            Wszystko · {KNOWLEDGE_ENTRIES.length}
          </button>
          {counts.map((option) => (
            <button
              key={option.value}
              type="button"
              onClick={() => setKind(option.value)}
              aria-pressed={kind === option.value}
              className={
                kind === option.value
                  ? "rounded-full border border-primary/40 bg-accent px-3 py-1 text-[12px] font-medium transition-colors"
                  : "rounded-full border border-border/60 px-3 py-1 text-[12px] text-muted-foreground transition-colors hover:bg-accent/60"
              }
            >
              {option.label} · {option.count}
            </button>
          ))}
        </div>
      </div>

      {visible.length === 0 ? (
        <Card>
          <CardContent className="px-4 py-12 text-center">
            <p className="text-[14px] font-medium">Brak wyników</p>
            <p className="mt-1.5 text-[13px] text-muted-foreground">
              Nic nie pasuje do „{query}”. Wiedza Evy jest jawną listą — możesz ją przeszukać inaczej albo poczekać na kolejne wątki.
            </p>
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-8">
          {KNOWLEDGE_KIND_ORDER.map((group) => {
            const entries = visible.filter((entry) => entry.kind === group);
            if (entries.length === 0) return null;
            return (
              <section key={group} aria-labelledby={`knowledge-${group}`}>
                <h2 id={`knowledge-${group}`} className="mb-3 text-[11px] font-medium uppercase tracking-[0.08em] text-subtle-foreground">
                  {KNOWLEDGE_KIND_LABELS[group]} · {entries.length}
                </h2>
                <div className="grid gap-4 sm:grid-cols-2">
                  {entries.map((entry) => (
                    <EntryCard key={entry.id} entry={entry} related={items ? relatedThreadCount(entry, items) : null} />
                  ))}
                </div>
              </section>
            );
          })}
        </div>
      )}

      <p className="mt-10 text-center text-[12px] text-subtle-foreground">
        Notatki pochodzą z demonstracyjnej skrzynki i kalendarza EVA. Licznik „w Attention” liczony jest na bieżąco z klasyfikacji.
      </p>
    </div>
  );
}
