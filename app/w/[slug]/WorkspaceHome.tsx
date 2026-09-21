"use client";

import { useEffect, useState } from "react";

import { SkeletonCards } from "@/components/states";
import { SegmentedControl, type Segment } from "@/components/ui";
import { loadWorkspaceView, saveWorkspaceView, type WorkspaceView } from "@/lib/keys";

import { DigestView } from "./DigestView";
import { RawView } from "./RawView";

const VIEWS: Array<Segment<WorkspaceView>> = [
  { id: "digest", label: "Digest", hint: "Grouped into events and filed under a theme" },
  { id: "raw", label: "Raw", hint: "Every article as it arrived, nothing written" },
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
      <SegmentedControl
        options={VIEWS}
        value={view}
        onChange={choose}
        label="How to read this workspace"
      />

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
