"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useCallback } from "react";

import { api } from "@/lib/api";
import { useAsync } from "@/lib/hooks";
import { SkeletonLine } from "@/components/states";
import { Badge, cx } from "@/components/ui";
import { plural, relativeTime } from "@/lib/format";

const TABS = [
  { segment: "", label: "Digest" },
  { segment: "articles", label: "Articles" },
  { segment: "sources", label: "Sources" },
  { segment: "chat", label: "Chat" },
  { segment: "settings", label: "Settings" },
];

/** Title, health line and tab bar shared by every page inside a workspace. */
export function WorkspaceChrome({ slug }: { slug: string }) {
  const pathname = usePathname() ?? "";
  const load = useCallback((signal: AbortSignal) => api.getWorkspace(slug, signal), [slug]);
  const { data, error } = useAsync(load, [slug]);

  const base = `/w/${encodeURIComponent(slug)}`;

  return (
    <div className="mb-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          {data ? (
            <h1 className="truncate text-xl font-semibold tracking-tight sm:text-2xl">
              {data.name}
            </h1>
          ) : error ? (
            <h1 className="truncate text-xl font-semibold tracking-tight sm:text-2xl">{slug}</h1>
          ) : (
            <SkeletonLine className="h-6 w-48" />
          )}
          <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted">
            {data ? (
              <>
                {/* The detail endpoint answers with the sources themselves
                    rather than a roll up, so counting them is the reliable
                    reading and the roll up is only a fallback. */}
                <span>{plural(data.sources?.length ?? data.source_count ?? 0, "source")}</span>
                {typeof data.article_count === "number" ? (
                  <>
                    <span aria-hidden="true">/</span>
                    <span>{plural(data.article_count, "article")}</span>
                  </>
                ) : null}
                <span aria-hidden="true">/</span>
                <span>
                  last run{" "}
                  {relativeTime(data.last_run_at ?? data.latest_run?.started_at ?? null)}
                </span>
              </>
            ) : null}
          </div>
        </div>
        {data && !data.enabled ? <Badge tone="warn">Paused</Badge> : null}
      </div>

      <nav
        aria-label="Workspace sections"
        className="mt-4 -mx-3 overflow-x-auto px-3 sm:mx-0 sm:px-0"
      >
        <ul className="flex min-w-max items-center gap-1 border-b border-line pb-px">
          {TABS.map((tab) => {
            const href = tab.segment ? `${base}/${tab.segment}` : base;
            const active = tab.segment
              ? pathname === href || pathname.startsWith(`${href}/`)
              : pathname === base;
            return (
              <li key={tab.label}>
                <Link
                  href={href}
                  aria-current={active ? "page" : undefined}
                  className={cx(
                    "-mb-px inline-block rounded-t-md border-b-2 px-3 py-2 text-sm font-medium transition-colors",
                    active
                      ? "border-accent text-ink"
                      : "border-transparent text-muted hover:text-ink",
                  )}
                >
                  {tab.label}
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>
    </div>
  );
}
