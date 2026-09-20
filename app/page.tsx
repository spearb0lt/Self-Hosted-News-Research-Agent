"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useState } from "react";

import { CreateWorkspacePanel } from "@/components/CreateWorkspacePanel";
import { EmptyState, ErrorState, SkeletonCards } from "@/components/states";
import { Badge, Button, Card } from "@/components/ui";
import { useConfig } from "@/components/ConfigProvider";
import { api } from "@/lib/api";
import { plural, relativeTime } from "@/lib/format";
import { useAsync } from "@/lib/hooks";
import type { Workspace } from "@/lib/types";

export default function HomePage() {
  const router = useRouter();
  const { config } = useConfig();
  const [creating, setCreating] = useState(false);

  const load = useCallback((signal: AbortSignal) => api.listWorkspaces(signal), []);
  const { data, error, initialising, reload } = useAsync(load, []);

  const workspaces = data ?? [];
  const tagline =
    config?.app.tagline ||
    "Collect the news you care about, group it into events, and read a short daily digest you can ask questions of.";

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">Workspaces</h1>
          <p className="mt-1 max-w-prose text-sm text-muted">{tagline}</p>
        </div>
        {!creating ? (
          <Button variant="primary" onClick={() => setCreating(true)}>
            New workspace
          </Button>
        ) : null}
      </div>

      {creating ? (
        <CreateWorkspacePanel
          onCancel={() => setCreating(false)}
          onCreated={(workspace: Workspace) => {
            setCreating(false);
            reload();
            router.push(`/w/${encodeURIComponent(workspace.slug)}`);
          }}
        />
      ) : null}

      {initialising ? (
        <SkeletonCards count={3} lines={2} />
      ) : error ? (
        <ErrorState
          error={error}
          onRetry={reload}
          title="Could not load your workspaces"
        />
      ) : workspaces.length === 0 ? (
        !creating ? (
          <EmptyState
            title="Nothing here yet"
            action={
              <Button variant="primary" onClick={() => setCreating(true)}>
                Create your first workspace
              </Button>
            }
          >
            <p>
              A workspace follows one beat. Point it at feeds, sites, newsletters or search
              terms, and it collects the articles, groups the ones that are about the same
              thing into events, writes a two sentence summary of each, and keeps an archive
              you can search or ask questions of.
            </p>
            <p className="mt-2">
              Start from a preset if you want a beat that is already wired up, or start blank
              and add your own sources.
            </p>
          </EmptyState>
        ) : null
      ) : (
        <ul className="grid gap-3 sm:grid-cols-2">
          {workspaces.map((workspace) => (
            <Card as="li" key={workspace.slug} className="transition-colors hover:border-line-strong">
              <Link
                href={`/w/${encodeURIComponent(workspace.slug)}`}
                className="flex h-full flex-col gap-3 rounded-xl p-4 sm:p-5"
              >
                <div className="flex items-start justify-between gap-3">
                  <h2 className="min-w-0 text-base font-semibold break-anywhere">
                    {workspace.name}
                  </h2>
                  {!workspace.enabled ? <Badge tone="warn">Paused</Badge> : null}
                </div>
                {workspace.description ? (
                  <p className="text-sm text-muted break-anywhere">{workspace.description}</p>
                ) : null}
                {workspace.themes.length ? (
                  <div className="flex flex-wrap gap-1">
                    {workspace.themes.slice(0, 5).map((theme) => (
                      <Badge key={theme.id} tone="accent" title={theme.hint || undefined}>
                        {theme.label}
                      </Badge>
                    ))}
                    {workspace.themes.length > 5 ? (
                      <Badge tone="neutral">+{workspace.themes.length - 5}</Badge>
                    ) : null}
                  </div>
                ) : null}
                <div className="mt-auto flex flex-wrap items-center gap-x-3 gap-y-1 pt-1 text-xs text-muted">
                  <span>{plural(workspace.source_count, "source")}</span>
                  <span aria-hidden="true">/</span>
                  <span>{plural(workspace.article_count, "article")}</span>
                  <span aria-hidden="true">/</span>
                  <span>last run {relativeTime(workspace.last_run_at)}</span>
                </div>
              </Link>
            </Card>
          ))}
        </ul>
      )}
    </div>
  );
}
