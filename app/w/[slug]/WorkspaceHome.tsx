"use client";

import { useEffect, useState } from "react";

import { SkeletonCards } from "@/components/states";
import { cx } from "@/components/ui";
import { loadWorkspaceView, saveWorkspaceView, type WorkspaceView } from "@/lib/keys";

import { DigestView } from "./DigestView";
import { RawView } from "./RawView";

const VIEWS: Array<{ id: WorkspaceView; label: string; hint: string }> = [
  { id: "digest", label: "Digest", hint: "Grouped into events and filed under a theme" },
  { id: "raw", label: "Raw", hint: "Every article, newest first, nothing written" },
];

/**
 * Picks how this workspace is read.
 *
 * The stored choice can only be read in the browser, so nothing is rendered
 * until it has been: starting on the digest and switching a frame later would
 * fire a request for events that a raw reader never wanted.
 */
export function WorkspaceHome({ slug }: { slug: string }) {
  const [view, setView] = useState<WorkspaceView | null>(null);

  useEffect(() => {
    setView(loadWorkspaceView(slug) ?? "digest");
  }, [slug]);

  const choose = (next: WorkspaceView) => {
    setView(next);
    saveWorkspaceView(slug, next);
  };

  return (
    <div className="flex flex-col gap-5">
      <div
        role="group"
        aria-label="How to read this workspace"
        className="inline-flex w-fit rounded-lg border border-line bg-surface p-0.5"
      >
        {VIEWS.map((entry) => (
          <button
            key={entry.id}
            type="button"
            onClick={() => choose(entry.id)}
            aria-pressed={view === entry.id}
            title={entry.hint}
            className={cx(
              "rounded-md px-3 py-1.5 text-sm font-medium transition-colors",
              view === entry.id ? "bg-accent text-accent-ink" : "text-muted hover:text-ink",
            )}
          >
            {entry.label}
          </button>
        ))}
      </div>

      {view === null ? (
        <SkeletonCards count={3} lines={3} />
      ) : view === "raw" ? (
        <RawView slug={slug} />
      ) : (
        <DigestView slug={slug} />
      )}
    </div>
  );
}
