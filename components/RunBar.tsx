"use client";

import { useState } from "react";

import { ErrorState } from "@/components/states";
import { ProviderPicker, useProviderSelection } from "@/components/ProviderPicker";
import { Badge, Button, Card, Toggle, cx } from "@/components/ui";
import { useConfig } from "@/components/ConfigProvider";
import { describeStats, humanise } from "@/lib/format";
import { MAX_AUTO_CONTINUES, useRun } from "@/lib/run";
import type { BadgeTone } from "@/components/ui";
import type { RunStatus } from "@/lib/types";

const STATUS_TONE: Record<RunStatus, BadgeTone> = {
  pending: "neutral",
  running: "accent",
  paused: "warn",
  done: "ok",
  failed: "danger",
};

const STATUS_LABEL: Record<RunStatus, string> = {
  pending: "Queued",
  running: "Running",
  paused: "Paused",
  done: "Finished",
  failed: "Failed",
};

/**
 * Starts a collection run and narrates it.
 *
 * The part that matters is `paused`. On a serverless host the run stops when
 * the invocation budget runs out and reports how far it got, and the client is
 * what restarts it. That is shown plainly rather than hidden, with automatic
 * resuming on by default and a manual Continue always available.
 */
export function RunBar({ slug, onFinished }: { slug: string; onFinished?: () => void }) {
  const { config } = useConfig();
  const { selection, setSelection, providers } = useProviderSelection();
  const controller = useRun(slug, { onFinished });
  const [showOptions, setShowOptions] = useState(false);

  const { run, error, busy, active, autoContinue, setAutoContinue, continuations } = controller;
  const budget = config?.runtime.max_job_seconds;
  const serverless = config?.runtime.tier === "serverless";

  const total = run?.progress?.total ?? 0;
  const done = run?.progress?.done ?? 0;
  const percent = total > 0 ? Math.min(100, Math.round((done / total) * 100)) : null;
  const stats = describeStats(run?.stats ?? null);
  const exhausted = continuations >= MAX_AUTO_CONTINUES;

  return (
    <Card className="p-4 sm:p-5">
      <div className="flex flex-wrap items-center gap-3">
        <Button
          variant="primary"
          onClick={() => controller.start(selection.provider ? selection : {})}
          loading={busy && !active}
          disabled={active || providers.length === 0}
        >
          {active ? "Run in progress" : "Run now"}
        </Button>

        <Button
          variant="ghost"
          size="sm"
          onClick={() => setShowOptions((value) => !value)}
          aria-expanded={showOptions}
        >
          {showOptions ? "Hide options" : "Options"}
        </Button>

        {run ? (
          <Badge tone={STATUS_TONE[run.status]}>{STATUS_LABEL[run.status]}</Badge>
        ) : null}

        {run && run.stage ? (
          <span className="text-sm text-muted">{humanise(run.stage)}</span>
        ) : null}

        {run && !active ? (
          <Button variant="ghost" size="sm" className="ml-auto" onClick={controller.dismiss}>
            Dismiss
          </Button>
        ) : null}
      </div>

      {showOptions ? (
        <div className="mt-4 flex flex-col gap-3 rounded-lg border border-line bg-raised p-3">
          <ProviderPicker
            selection={selection}
            onChange={setSelection}
            providers={providers}
          />
          <Toggle
            checked={autoContinue}
            onChange={setAutoContinue}
            label="Continue automatically when a run pauses"
            description={
              serverless
                ? `Each invocation here gets about ${budget ?? 0} seconds. A longer run pauses and is picked up again from the browser.`
                : "Long runs pause and resume if the backend reports a time budget."
            }
          />
        </div>
      ) : null}

      {/* Everything the run says about itself is announced, not just painted. */}
      <div aria-live="polite" aria-atomic="false" className={run ? "mt-4" : "sr-only"}>
        {run ? (
          <div className="flex flex-col gap-3">
            <p className="sr-only">
              Run {STATUS_LABEL[run.status].toLowerCase()}
              {run.stage ? `, stage ${humanise(run.stage)}` : ""}
              {total > 0 ? `, ${done} of ${total} done` : ""}.
            </p>

            <div
              className="h-2 w-full overflow-hidden rounded-full bg-raised"
              role="progressbar"
              aria-label="Run progress"
              aria-valuemin={0}
              aria-valuemax={total > 0 ? total : undefined}
              aria-valuenow={total > 0 ? done : undefined}
              aria-valuetext={total > 0 ? `${done} of ${total}` : STATUS_LABEL[run.status]}
            >
              <div
                className={cx(
                  "h-full rounded-full transition-[width] duration-500",
                  run.status === "failed"
                    ? "bg-danger"
                    : run.status === "paused"
                      ? "bg-warn"
                      : run.status === "done"
                        ? "bg-ok"
                        : "bg-accent",
                )}
                style={{ width: percent === null ? (active ? "35%" : "100%") : `${percent}%` }}
              />
            </div>

            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted">
              {total > 0 ? (
                <span>
                  {done} of {total}
                </span>
              ) : null}
              {stats.map((entry) => (
                <span key={entry}>{entry}</span>
              ))}
              {continuations > 0 ? (
                <span>
                  resumed {continuations} {continuations === 1 ? "time" : "times"}
                </span>
              ) : null}
            </div>

            {run.status === "paused" ? (
              <div className="rounded-lg border border-transparent bg-warn-soft p-3">
                <p className="text-sm font-semibold text-warn">
                  Paused to stay inside the time budget
                </p>
                <p className="mt-1 text-sm text-ink/85">
                  The run saved its place at {humanise(run.stage) || "the current stage"} and is
                  ready to carry on.{" "}
                  {autoContinue && !exhausted
                    ? "Continuing automatically."
                    : exhausted
                      ? "Automatic resuming stopped after many pauses, so continue it by hand."
                      : "Automatic resuming is off."}
                </p>
                <div className="mt-3 flex flex-wrap items-center gap-2">
                  <Button
                    size="sm"
                    variant="primary"
                    onClick={controller.resume}
                    loading={busy}
                  >
                    Continue
                  </Button>
                  {!autoContinue || exhausted ? (
                    <Button
                      size="sm"
                      variant="secondary"
                      onClick={() => setAutoContinue(true)}
                      disabled={autoContinue && exhausted}
                    >
                      Resume automatically
                    </Button>
                  ) : (
                    <Button size="sm" variant="ghost" onClick={() => setAutoContinue(false)}>
                      Stop resuming automatically
                    </Button>
                  )}
                </div>
              </div>
            ) : null}

            {run.status === "failed" && run.error ? (
              <div role="alert" className="rounded-lg bg-danger-soft p-3">
                <p className="text-sm font-semibold text-danger">The run failed</p>
                <p className="mt-1 text-sm text-ink/85 break-anywhere">{run.error}</p>
              </div>
            ) : null}

            {run.status === "done" ? (
              <p className="text-sm text-ok">
                Finished. The digest below reflects everything collected.
              </p>
            ) : null}
          </div>
        ) : null}
      </div>

      {error ? (
        <div className="mt-4">
          <ErrorState error={error} compact onRetry={run ? controller.resume : undefined} />
        </div>
      ) : null}

      {providers.length === 0 ? (
        <p className="mt-3 text-sm text-muted">
          A run needs an AI provider for grouping and summarising. Add a key in Settings first.
        </p>
      ) : null}
    </Card>
  );
}
