"use client";

import Link from "next/link";
import { useCallback, useMemo, useState } from "react";

import { EmptyState, ErrorState, SkeletonCards } from "@/components/states";
import { Badge, Button, Card, Select, SectionHeading } from "@/components/ui";
import { linkButtonClass } from "@/lib/classes";
import { ApiError, api, toApiError } from "@/lib/api";
import { absoluteDateTime, compactNumber, humanise, money } from "@/lib/format";
import { useAsync } from "@/lib/hooks";
import type { Usage, UsageRow, UsageRun, Workspace } from "@/lib/types";

const ALL = "__all__";

interface CombinedUsage {
  total: number;
  rows: UsageRow[];
  runs: Array<UsageRun & { workspace: string }>;
  failures: Array<{ workspace: string; error: ApiError }>;
}

function mergeRows(rows: UsageRow[]): UsageRow[] {
  const merged = new Map<string, UsageRow>();
  for (const row of rows) {
    const key = `${row.provider}|${row.model}|${row.kind}`;
    const existing = merged.get(key);
    if (!existing) {
      merged.set(key, { ...row });
      continue;
    }
    existing.calls += row.calls ?? 0;
    existing.input_tokens += row.input_tokens ?? 0;
    existing.output_tokens += row.output_tokens ?? 0;
    existing.cost_usd += row.cost_usd ?? 0;
  }
  return Array.from(merged.values()).sort((a, b) => b.cost_usd - a.cost_usd);
}

export default function UsagePage() {
  const [scope, setScope] = useState(ALL);

  const loadWorkspaces = useCallback((signal: AbortSignal) => api.listWorkspaces(signal), []);
  const workspacesState = useAsync(loadWorkspaces, []);
  const workspaces = useMemo<Workspace[]>(() => workspacesState.data ?? [], [workspacesState.data]);

  const slugs = useMemo(
    () => (scope === ALL ? workspaces.map((workspace) => workspace.slug) : [scope]),
    [scope, workspaces],
  );
  const slugKey = slugs.join(",");

  // The contract meters usage per workspace, so an account wide figure is the
  // sum of each one. A workspace that fails to report is named rather than
  // quietly dropped from the total.
  const loadUsage = useCallback(
    async (signal: AbortSignal): Promise<CombinedUsage> => {
      if (slugs.length === 0) {
        return { total: 0, rows: [], runs: [], failures: [] };
      }
      const settled = await Promise.all(
        slugs.map(async (slug) => {
          try {
            const usage = await api.usage(slug, signal);
            return { slug, usage, error: null as ApiError | null };
          } catch (cause) {
            if (cause instanceof DOMException && cause.name === "AbortError") throw cause;
            return { slug, usage: null as Usage | null, error: toApiError(cause) };
          }
        }),
      );

      const rows: UsageRow[] = [];
      const runs: Array<UsageRun & { workspace: string }> = [];
      const failures: Array<{ workspace: string; error: ApiError }> = [];
      let total = 0;

      for (const entry of settled) {
        if (entry.error || !entry.usage) {
          if (entry.error) failures.push({ workspace: entry.slug, error: entry.error });
          continue;
        }
        total += entry.usage.total_cost_usd ?? 0;
        rows.push(...(entry.usage.by_provider ?? []));
        for (const run of entry.usage.recent_runs ?? []) {
          runs.push({ ...run, workspace: entry.slug });
        }
      }

      runs.sort((a, b) => {
        const left = a.started_at ? Date.parse(a.started_at) : 0;
        const right = b.started_at ? Date.parse(b.started_at) : 0;
        return right - left;
      });

      return { total, rows: mergeRows(rows), runs: runs.slice(0, 20), failures };
    },
    [slugs],
  );

  const usageState = useAsync(loadUsage, [slugKey]);
  const usage = usageState.data;
  const maxCost = usage ? Math.max(...usage.rows.map((row) => row.cost_usd), 0) : 0;

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">Usage and cost</h1>
          <p className="mt-1 max-w-prose text-sm text-muted">
            What the model calls behind your runs, digests and questions have cost so far.
          </p>
        </div>
        <Button size="sm" variant="secondary" onClick={usageState.reload}>
          Refresh
        </Button>
      </div>

      {workspacesState.error ? (
        <ErrorState
          error={workspacesState.error}
          onRetry={workspacesState.reload}
          title="Could not list your workspaces"
        />
      ) : null}

      {workspaces.length > 1 ? (
        <Select
          label="Scope"
          value={scope}
          onChange={(event) => setScope(event.target.value)}
          className="sm:max-w-72"
        >
          <option value={ALL}>All workspaces</option>
          {workspaces.map((workspace) => (
            <option key={workspace.slug} value={workspace.slug}>
              {workspace.name}
            </option>
          ))}
        </Select>
      ) : null}

      {workspacesState.initialising || usageState.initialising ? (
        <SkeletonCards count={2} lines={4} />
      ) : workspaces.length === 0 ? (
        <EmptyState
          title="No workspaces yet"
          action={
            <Link href="/" className={linkButtonClass("primary")}>
              Create one
            </Link>
          }
        >
          <p>Cost is metered per workspace, so there is nothing to show until you have one.</p>
        </EmptyState>
      ) : usageState.error ? (
        <ErrorState
          error={usageState.error}
          onRetry={usageState.reload}
          title="Could not load usage"
        />
      ) : usage ? (
        <>
          <Card className="p-4 sm:p-6">
            <p className="text-sm text-muted">
              Total spent{scope === ALL ? " across all workspaces" : ""}
            </p>
            <p className="mt-1 text-3xl font-semibold tabular-nums">{money(usage.total)}</p>
            <p className="mt-2 text-xs text-muted">
              Local providers cost nothing and are metered at zero.
            </p>
          </Card>

          {usage.failures.length > 0 ? (
            <Card className="p-4 sm:p-5">
              <SectionHeading
                title="Some workspaces did not report"
                description="Their spend is missing from the total above."
              />
              <ul className="flex flex-col gap-2">
                {usage.failures.map((failure) => (
                  <li key={failure.workspace}>
                    <ErrorState
                      error={failure.error}
                      compact
                      title={`${failure.workspace} could not be metered`}
                    />
                  </li>
                ))}
              </ul>
            </Card>
          ) : null}

          <Card className="p-4 sm:p-6">
            <SectionHeading title="By provider and model" />
            {usage.rows.length === 0 ? (
              <p className="text-sm text-muted">
                Nothing has been billed yet. Run a workspace and this fills in.
              </p>
            ) : (
              <div className="-mx-4 overflow-x-auto px-4 sm:mx-0 sm:px-0">
                <table className="w-full min-w-[34rem] border-collapse text-sm">
                  <caption className="sr-only">Cost broken down by provider and model</caption>
                  <thead>
                    <tr className="border-b border-line text-left text-xs tracking-wide text-muted uppercase">
                      <th scope="col" className="py-2 pr-3 font-medium">
                        Provider
                      </th>
                      <th scope="col" className="py-2 pr-3 font-medium">
                        Model
                      </th>
                      <th scope="col" className="py-2 pr-3 font-medium">
                        Kind
                      </th>
                      <th scope="col" className="py-2 pr-3 text-right font-medium">
                        Calls
                      </th>
                      <th scope="col" className="py-2 pr-3 text-right font-medium">
                        Tokens in
                      </th>
                      <th scope="col" className="py-2 pr-3 text-right font-medium">
                        Tokens out
                      </th>
                      <th scope="col" className="py-2 text-right font-medium">
                        Cost
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {usage.rows.map((row) => (
                      <tr
                        key={`${row.provider}-${row.model}-${row.kind}`}
                        className="border-b border-line last:border-0"
                      >
                        <th scope="row" className="py-2.5 pr-3 text-left font-medium break-anywhere">
                          {row.provider}
                        </th>
                        <td className="py-2.5 pr-3 text-muted break-anywhere">{row.model}</td>
                        <td className="py-2.5 pr-3">
                          <Badge tone="neutral">{row.kind}</Badge>
                        </td>
                        <td className="py-2.5 pr-3 text-right tabular-nums">
                          {compactNumber(row.calls)}
                        </td>
                        <td className="py-2.5 pr-3 text-right tabular-nums">
                          {compactNumber(row.input_tokens)}
                        </td>
                        <td className="py-2.5 pr-3 text-right tabular-nums">
                          {compactNumber(row.output_tokens)}
                        </td>
                        <td className="py-2.5 text-right tabular-nums">
                          <span className="inline-flex items-center justify-end gap-2">
                            <span
                              aria-hidden="true"
                              className="hidden h-1.5 rounded-full bg-accent sm:block"
                              style={{
                                width:
                                  maxCost > 0
                                    ? `${Math.max(4, Math.round((row.cost_usd / maxCost) * 56))}px`
                                    : "4px",
                              }}
                            />
                            {money(row.cost_usd)}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>

          <Card className="p-4 sm:p-6">
            <SectionHeading title="Recent runs" />
            {usage.runs.length === 0 ? (
              <p className="text-sm text-muted">No runs recorded yet.</p>
            ) : (
              <ul className="flex flex-col gap-2">
                {usage.runs.map((run, index) => (
                  <li
                    key={`${run.workspace}-${String(run.id)}-${index}`}
                    className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border border-line p-3 text-sm"
                  >
                    <span className="font-medium break-anywhere">{run.workspace}</span>
                    {run.status ? (
                      <Badge
                        tone={
                          run.status === "failed"
                            ? "danger"
                            : run.status === "paused"
                              ? "warn"
                              : run.status === "done"
                                ? "ok"
                                : "neutral"
                        }
                      >
                        {run.status}
                      </Badge>
                    ) : null}
                    {run.stage ? <span className="text-muted">{humanise(run.stage)}</span> : null}
                    {run.provider ? (
                      <span className="text-muted break-anywhere">
                        {run.provider}
                        {run.model ? ` / ${run.model}` : ""}
                      </span>
                    ) : null}
                    {run.started_at ? (
                      <span className="text-xs text-faint">
                        {absoluteDateTime(run.started_at)}
                      </span>
                    ) : null}
                    {typeof run.cost_usd === "number" ? (
                      <span className="ml-auto tabular-nums">{money(run.cost_usd)}</span>
                    ) : null}
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </>
      ) : null}
    </div>
  );
}
