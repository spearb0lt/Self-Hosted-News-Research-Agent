"use client";

import { useCallback, useMemo, useState } from "react";

import { ErrorState, SkeletonRows } from "@/components/states";
import { Badge, Button, Card, Select } from "@/components/ui";
import { api } from "@/lib/api";
import { plural } from "@/lib/format";
import { useAsync } from "@/lib/hooks";
import type { Entity, EntityKind } from "@/lib/types";

/** How many rows are worth reading at a glance. The rest are behind a press. */
const TOP = 12;

const KINDS: Array<{ id: EntityKind | ""; label: string }> = [
  { id: "", label: "Everything" },
  { id: "person", label: "People" },
  { id: "org", label: "Organisations" },
  { id: "place", label: "Places" },
  { id: "ticker", label: "Tickers" },
  { id: "other", label: "Other" },
];

const WINDOWS: Array<{ hours: number; label: string }> = [
  { hours: 24, label: "Last 24 hours" },
  { hours: 72, label: "Last 3 days" },
  { hours: 168, label: "Last 7 days" },
  { hours: 720, label: "Last 30 days" },
];

const KIND_LABELS: Record<EntityKind, string> = {
  person: "person",
  org: "organisation",
  place: "place",
  ticker: "ticker",
  other: "other",
};

/**
 * Who and what this beat has actually been about.
 *
 * One measure, one colour: the bar carries magnitude and nothing else, so it
 * needs no legend, and every row is labelled with its own exact count as well,
 * which is what a reader compares when two bars are close.
 */
export function EntityLeaderboard({ slug }: { slug: string }) {
  const [kind, setKind] = useState<EntityKind | "">("");
  const [hours, setHours] = useState(168);
  const [showAll, setShowAll] = useState(false);

  const load = useCallback(
    (signal: AbortSignal) => api.entities(slug, { kind: kind || undefined, hours }, signal),
    [slug, kind, hours],
  );
  const { data, error, initialising, reload } = useAsync(load, [slug, kind, hours]);

  const entities = useMemo<Entity[]>(() => data?.entities ?? [], [data]);
  const visible = showAll ? entities : entities.slice(0, TOP);
  const top = entities[0]?.count ?? 0;

  return (
    <Card className="p-4 sm:p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="text-base font-semibold tracking-tight">Most covered</h3>
          <p className="mt-1 text-sm text-muted">
            The names enrichment pulled out of the articles, counted by how many mentioned them.
          </p>
        </div>
        <Button size="sm" variant="ghost" onClick={reload}>
          Refresh
        </Button>
      </div>

      <div className="mt-3 flex flex-wrap gap-2">
        <Select
          value={kind}
          onChange={(event) => {
            setKind(event.target.value as EntityKind | "");
            setShowAll(false);
          }}
          aria-label="Filter by kind of name"
          className="sm:max-w-48"
        >
          {KINDS.map((entry) => (
            <option key={entry.id || "all"} value={entry.id}>
              {entry.label}
            </option>
          ))}
        </Select>
        <Select
          value={String(hours)}
          onChange={(event) => {
            setHours(Number(event.target.value));
            setShowAll(false);
          }}
          aria-label="Time window"
          className="sm:max-w-48"
        >
          {WINDOWS.map((entry) => (
            <option key={entry.hours} value={entry.hours}>
              {entry.label}
            </option>
          ))}
        </Select>
      </div>

      <div className="mt-4">
        {initialising ? (
          <SkeletonRows count={5} />
        ) : error ? (
          <ErrorState error={error} onRetry={reload} compact title="Could not load the names" />
        ) : entities.length === 0 ? (
          <p className="text-sm text-muted">
            Nothing named in this window. Entities are written by the enrichment stage, so a
            workspace that has not run it yet has none.
          </p>
        ) : (
          <>
            <ol className="flex flex-col gap-2">
              {visible.map((entity, index) => (
                <li
                  key={`${String(entity.id)}-${entity.kind}`}
                  className="flex flex-wrap items-center gap-x-3 gap-y-1"
                  title={`${entity.name}: ${plural(entity.count, "article")} in this window`}
                >
                  <span className="w-5 shrink-0 text-right text-xs tabular-nums text-faint">
                    {index + 1}
                  </span>
                  <span className="min-w-0 flex-1 text-sm font-medium break-anywhere">
                    {entity.name}
                  </span>
                  {kind === "" ? (
                    <Badge tone="neutral">{KIND_LABELS[entity.kind] ?? entity.kind}</Badge>
                  ) : null}
                  <span className="flex min-w-24 shrink-0 items-center justify-end gap-2">
                    <span
                      aria-hidden="true"
                      className="h-1.5 rounded-full bg-accent"
                      style={{
                        width: top > 0 ? `${Math.max(4, Math.round((entity.count / top) * 72))}px` : "4px",
                      }}
                    />
                    <span className="w-6 text-right text-sm tabular-nums">{entity.count}</span>
                  </span>
                </li>
              ))}
            </ol>

            {entities.length > TOP ? (
              <div className="mt-3">
                <button
                  type="button"
                  onClick={() => setShowAll((value) => !value)}
                  aria-expanded={showAll}
                  className="rounded-md text-sm font-medium text-accent underline underline-offset-2"
                >
                  {showAll ? `Show the top ${TOP}` : `Show all ${entities.length} names`}
                </button>
              </div>
            ) : null}
          </>
        )}
      </div>
    </Card>
  );
}
