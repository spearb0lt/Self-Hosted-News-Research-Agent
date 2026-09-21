"use client";

import Link from "next/link";
import { useCallback, useMemo, useState } from "react";

import { EntityLeaderboard } from "@/components/EntityLeaderboard";
import { RunBar } from "@/components/RunBar";
import { EmptyState, ErrorState, SkeletonCards } from "@/components/states";
import { Badge, Button, Card, SegmentedControl, cx, type Segment } from "@/components/ui";
import { linkButtonClass } from "@/lib/classes";
import { api } from "@/lib/api";
import { absoluteDateTime, hostOf, plural, relativeTime, themeLabel } from "@/lib/format";
import { useAsync, useDisplayPrefs } from "@/lib/hooks";
import { loadEventSort, saveEventSort, type DisplayPrefs } from "@/lib/keys";
import type { Article, EventSort, EventSpread, NewsEvent, Theme } from "@/lib/types";

const PAGE_SIZE = 60;
const UNGROUPED = "";
const UNGROUPED_LABEL = "Everything else";

// "Top stories" rather than "importance": what the backend ranks by is how
// many outlets carried the same story, which is what a reader means by it.
const SORTS: Array<Segment<EventSort>> = [
  { id: "importance", label: "Top stories", hint: "Ranked by how many outlets carried the story" },
  { id: "newest", label: "Newest", hint: "Most recently updated first" },
  { id: "oldest", label: "Oldest", hint: "Longest running first" },
];

interface ThemeGroup {
  id: string;
  label: string;
  events: NewsEvent[];
}

export function DigestView({ slug }: { slug: string }) {
  // The chosen theme is an id, because that is what an event carries and what
  // the events endpoint filters on. Only labels are ever shown.
  const [theme, setTheme] = useState("");
  // Read straight out of storage, as in RawView: this view only ever mounts in
  // the browser, after WorkspaceHome has resolved its own stored choice.
  const [sort, setSort] = useState<EventSort>(() => loadEventSort(slug) ?? "importance");
  const display = useDisplayPrefs();

  const chooseSort = (next: EventSort) => {
    setSort(next);
    saveEventSort(slug, next);
  };

  const loadWorkspace = useCallback(
    (signal: AbortSignal) => api.getWorkspace(slug, signal),
    [slug],
  );
  const workspace = useAsync(loadWorkspace, [slug]);
  const taxonomy = useMemo<Theme[]>(() => workspace.data?.themes ?? [], [workspace.data]);

  const load = useCallback(
    (signal: AbortSignal) =>
      api.events(slug, { theme: theme || undefined, limit: PAGE_SIZE, sort }, signal),
    [slug, theme, sort],
  );
  const { data, error, initialising, reload } = useAsync(load, [slug, theme, sort]);

  const events = useMemo(() => data?.events ?? [], [data]);

  // The chips list the ids that actually arrived, so a filter never points at a
  // theme with nothing behind it, and each one is labelled from the taxonomy.
  const themes = useMemo<Theme[]>(() => {
    const seen = new Set<string>();
    for (const event of events) {
      if (event.theme) seen.add(event.theme);
    }
    // Keep the workspace's own order for the themes it knows, then anything
    // the classifier produced that is no longer in the taxonomy.
    const known = taxonomy.filter((entry) => seen.has(entry.id));
    const knownIds = new Set(known.map((entry) => entry.id));
    const orphans = Array.from(seen)
      .filter((id) => !knownIds.has(id))
      .sort((a, b) => a.localeCompare(b))
      .map<Theme>((id) => ({ id, label: id, hint: "" }));
    return [...known, ...orphans];
  }, [events, taxonomy]);

  const grouped = useMemo<ThemeGroup[]>(() => {
    const buckets = new Map<string, NewsEvent[]>();
    for (const event of events) {
      const key = event.theme || UNGROUPED;
      const bucket = buckets.get(key);
      if (bucket) bucket.push(event);
      else buckets.set(key, [event]);
    }

    const order = new Map(themes.map((entry, index) => [entry.id, index]));
    return Array.from(buckets.entries())
      .map<ThemeGroup>(([id, themeEvents]) => ({
        id,
        label: id ? themeLabel(taxonomy, id) : UNGROUPED_LABEL,
        events: themeEvents,
      }))
      .sort((a, b) => {
        if (a.id === UNGROUPED) return 1;
        if (b.id === UNGROUPED) return -1;
        return (order.get(a.id) ?? Number.MAX_SAFE_INTEGER) - (order.get(b.id) ?? Number.MAX_SAFE_INTEGER);
      });
  }, [events, themes, taxonomy]);

  return (
    <div className="flex flex-col gap-6">
      <RunBar slug={slug} onFinished={reload} />

      <div className="flex flex-wrap items-center gap-2">
        <h2 className="mr-auto text-base font-semibold tracking-tight">
          Today&apos;s events
          {data ? <span className="ml-2 text-sm font-normal text-muted">{data.total}</span> : null}
        </h2>
        <SegmentedControl
          options={SORTS}
          value={sort}
          onChange={chooseSort}
          label="Order the events"
        />
        <a
          href={api.feedUrl(slug)}
          className="rounded-md px-2 py-1 text-sm font-medium text-accent underline underline-offset-2"
        >
          RSS feed
        </a>
        <Button size="sm" variant="secondary" onClick={reload}>
          Refresh
        </Button>
      </div>

      {themes.length > 1 || theme ? (
        <div
          role="group"
          aria-label="Filter by theme"
          className="-mx-3 flex gap-1.5 overflow-x-auto px-3 pb-1 sm:mx-0 sm:flex-wrap sm:px-0"
        >
          <ThemeChip label="All themes" active={theme === ""} onClick={() => setTheme("")} />
          {themes.map((entry) => (
            <ThemeChip
              key={entry.id}
              label={entry.label}
              title={entry.hint || undefined}
              active={theme === entry.id}
              onClick={() => setTheme(entry.id)}
            />
          ))}
        </div>
      ) : null}

      {display.entities ? <EntityLeaderboard slug={slug} /> : null}

      {initialising ? (
        <SkeletonCards count={4} lines={3} />
      ) : error ? (
        <ErrorState error={error} onRetry={reload} title="Could not load the digest" />
      ) : events.length === 0 ? (
        <EmptyState
          title={theme ? `Nothing under ${themeLabel(taxonomy, theme)} yet` : "No events yet"}
          action={
            theme ? (
              <Button variant="secondary" onClick={() => setTheme("")}>
                Show all themes
              </Button>
            ) : (
              <Link
                href={`/w/${encodeURIComponent(slug)}/sources`}
                className={linkButtonClass("secondary")}
              >
                Add sources
              </Link>
            )
          }
        >
          {theme ? (
            <p>Nothing has been filed under this theme in the current window.</p>
          ) : (
            <p>
              Events appear after a run collects articles and groups the ones covering the same
              story. Add a source or two, then press Run now above.
            </p>
          )}
        </EmptyState>
      ) : (
        <div className="flex flex-col gap-8">
          {grouped.map((group) => (
            <section
              key={group.id || "ungrouped"}
              aria-labelledby={`theme-${group.id || "ungrouped"}`}
            >
              <div className="mb-3 flex items-center gap-3">
                <h3
                  id={`theme-${group.id || "ungrouped"}`}
                  className="text-sm font-semibold tracking-wide text-muted uppercase"
                >
                  {group.label}
                </h3>
                <span className="text-xs text-faint">{plural(group.events.length, "event")}</span>
              </div>
              <ul className="flex flex-col gap-3">
                {group.events.map((event) => (
                  <EventCard key={String(event.id)} event={event} display={display} />
                ))}
              </ul>
            </section>
          ))}
        </div>
      )}
    </div>
  );
}

function ThemeChip({
  label,
  active,
  onClick,
  title,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
  title?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      title={title}
      className={cx(
        "shrink-0 rounded-full border px-3 py-1 text-sm font-medium transition-colors",
        active
          ? "border-transparent bg-accent text-accent-ink"
          : "border-line bg-surface text-muted hover:text-ink",
      )}
    >
      {label}
    </button>
  );
}

function EventCard({ event, display }: { event: NewsEvent; display: DisplayPrefs }) {
  const [expanded, setExpanded] = useState(false);
  const articles = event.articles ?? [];
  const visible = expanded ? articles : articles.slice(0, 3);

  // An empty string is the common case for both of these, so each one is asked
  // about separately rather than the pair being rendered as a block.
  const meta = event.meta ?? {};
  const whyItMatters = display.eventCommentary ? (meta.why_it_matters ?? "").trim() : "";
  const disagreement = display.eventCommentary ? (meta.disagreement ?? "").trim() : "";
  const spread = display.contested ? (meta.spread ?? null) : null;

  return (
    <Card as="li" className="p-4 sm:p-5">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <h4 className="min-w-0 flex-1 text-base font-semibold break-anywhere">{event.title}</h4>
        <div className="flex shrink-0 flex-wrap items-center gap-1.5">
          {spread?.contested ? (
            <Badge
              tone="warn"
              title={`Outlets covering this differed in tone by ${spread.sentiment_spread.toFixed(2)}`}
            >
              Contested
            </Badge>
          ) : null}
          <Badge
            tone="neutral"
            title={`${event.source_count} distinct sources covered this`}
          >
            {plural(event.source_count, "source")}
          </Badge>
          {typeof event.importance === "number" ? (
            <Badge tone={event.importance >= 4 ? "warn" : "neutral"} title="Importance">
              {"*".repeat(Math.max(1, Math.min(5, Math.round(event.importance))))}
            </Badge>
          ) : null}
        </div>
      </div>

      {event.summary ? (
        <p className="mt-2 text-sm leading-relaxed text-ink/90 break-anywhere">{event.summary}</p>
      ) : null}

      {whyItMatters || disagreement ? (
        <dl className="mt-3 flex flex-col gap-2">
          {whyItMatters ? (
            <div className="rounded-r-lg border-l-2 border-accent bg-accent-soft px-3 py-2">
              <dt className="text-xs font-semibold tracking-wide text-accent uppercase">
                Why it matters
              </dt>
              <dd className="mt-0.5 text-sm text-ink/90 break-anywhere">{whyItMatters}</dd>
            </div>
          ) : null}
          {disagreement ? (
            <div className="rounded-r-lg border-l-2 border-warn bg-warn-soft px-3 py-2">
              <dt className="text-xs font-semibold tracking-wide text-warn uppercase">
                Where the accounts differ
              </dt>
              <dd className="mt-0.5 text-sm text-ink/90 break-anywhere">{disagreement}</dd>
            </div>
          ) : null}
        </dl>
      ) : null}

      {spread ? <SpreadPanel spread={spread} /> : null}

      <p className="mt-2 text-xs text-faint">
        {event.first_seen ? <>first seen {relativeTime(event.first_seen)}</> : null}
        {event.first_seen && event.last_seen ? " / " : null}
        {event.last_seen ? <>last update {relativeTime(event.last_seen)}</> : null}
      </p>

      {articles.length ? (
        <ul className="mt-3 flex flex-col gap-1.5 border-t border-line pt-3">
          {visible.map((article) => (
            <ArticleLink key={String(article.id)} article={article} />
          ))}
          {articles.length > 3 ? (
            <li>
              <button
                type="button"
                onClick={() => setExpanded((value) => !value)}
                className="rounded-md text-sm font-medium text-accent underline underline-offset-2"
                aria-expanded={expanded}
              >
                {expanded ? "Show fewer" : `Show all ${articles.length} articles`}
              </button>
            </li>
          ) : null}
        </ul>
      ) : null}
    </Card>
  );
}

/**
 * How the same story read across the outlets that carried it.
 *
 * Deduplicating is what earns this panel: a single feed can only ever show one
 * telling, so the comparison is the thing this app can say and a reader cannot
 * get anywhere else. It is offered whenever more than one outlet covered the
 * event, and says so loudly once their tone is far enough apart to be contested.
 */
function SpreadPanel({ spread }: { spread: EventSpread }) {
  const [open, setOpen] = useState(false);
  const domains = spread.domains ?? [];
  if (!spread.contested && spread.distinct_domains < 2) return null;

  const busiest = domains[0]?.[1] ?? 0;

  return (
    <div
      className={cx(
        "mt-3 rounded-lg border px-3 py-2",
        spread.contested ? "border-warn bg-warn-soft" : "border-line",
      )}
    >
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="flex w-full flex-wrap items-center gap-x-3 gap-y-1 rounded-md text-left"
      >
        <span
          className={cx(
            "text-sm font-semibold",
            spread.contested ? "text-warn" : "text-ink",
          )}
        >
          {spread.contested
            ? "Outlets told this differently"
            : `Carried by ${plural(spread.distinct_domains, "outlet")}`}
        </span>
        <span className="ml-auto text-sm font-medium text-accent underline underline-offset-2">
          {open ? "Hide the spread" : "See the spread"}
        </span>
      </button>

      {spread.contested ? (
        <p className="mt-1 text-sm text-ink/85">
          Their tone was {spread.sentiment_spread.toFixed(2)} apart, past the 0.8 this pipeline
          treats as real disagreement rather than house style.
        </p>
      ) : null}

      {open ? (
        <div className="mt-3 border-t border-line pt-3">
          <ul className="flex flex-col gap-1.5">
            {domains.map(([domain, count]) => (
              <li key={domain} className="flex items-center gap-x-3 text-sm">
                <span className="min-w-0 flex-1 break-anywhere">{domain}</span>
                <span className="flex shrink-0 items-center gap-2">
                  <span
                    aria-hidden="true"
                    className="h-1.5 rounded-full bg-accent"
                    style={{
                      width:
                        busiest > 0 ? `${Math.max(4, Math.round((count / busiest) * 48))}px` : "4px",
                    }}
                  />
                  <span className="w-6 text-right tabular-nums text-muted">{count}</span>
                </span>
              </li>
            ))}
          </ul>

          <dl className="mt-3 flex flex-wrap gap-x-6 gap-y-1 text-xs text-muted">
            <div className="flex gap-1">
              <dt>Tone spread</dt>
              <dd className="font-medium text-ink tabular-nums">
                {spread.sentiment_spread.toFixed(2)}
              </dd>
            </div>
            <div className="flex gap-1">
              <dt>Average tone</dt>
              <dd className="font-medium text-ink tabular-nums">
                {spread.mean_sentiment === null ? "not scored" : spread.mean_sentiment.toFixed(2)}
              </dd>
            </div>
          </dl>
        </div>
      ) : null}
    </div>
  );
}

function ArticleLink({ article }: { article: Article }) {
  return (
    <li className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 text-sm">
      <a
        href={article.url}
        target="_blank"
        rel="noopener noreferrer"
        className="min-w-0 rounded-sm text-accent underline underline-offset-2 break-anywhere"
      >
        {article.title || article.url}
      </a>
      <span className="text-xs text-faint">
        {article.domain || hostOf(article.url)}
        {article.published_at ? ` / ${absoluteDateTime(article.published_at)}` : ""}
      </span>
    </li>
  );
}

