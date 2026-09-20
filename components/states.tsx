"use client";

import type { ReactNode } from "react";

import type { ApiError } from "@/lib/api";
import { Button, Card, cx } from "@/components/ui";

/* -------------------------------------------------------------------------- */
/* Skeletons                                                                   */
/* -------------------------------------------------------------------------- */

export function SkeletonLine({ className }: { className?: string }) {
  return <div className={cx("skeleton h-3.5", className)} aria-hidden="true" />;
}

export function SkeletonBlock({ className }: { className?: string }) {
  return <div className={cx("skeleton", className)} aria-hidden="true" />;
}

/** A stack of card shaped placeholders, shaped like the content it stands in for. */
export function SkeletonCards({ count = 3, lines = 3 }: { count?: number; lines?: number }) {
  return (
    <div className="flex flex-col gap-3" role="status" aria-label="Loading">
      <span className="sr-only">Loading</span>
      {Array.from({ length: count }, (_, cardIndex) => (
        <Card key={cardIndex} className="p-4 sm:p-5">
          <SkeletonLine className="w-2/5" />
          <div className="mt-3 flex flex-col gap-2">
            {Array.from({ length: lines }, (_, lineIndex) => (
              <SkeletonLine
                key={lineIndex}
                className={lineIndex === lines - 1 ? "w-3/5" : "w-full"}
              />
            ))}
          </div>
        </Card>
      ))}
    </div>
  );
}

export function SkeletonRows({ count = 5 }: { count?: number }) {
  return (
    <div className="flex flex-col gap-2" role="status" aria-label="Loading">
      <span className="sr-only">Loading</span>
      {Array.from({ length: count }, (_, index) => (
        <div key={index} className="flex items-center gap-3 rounded-lg border border-line p-3">
          <SkeletonBlock className="size-8 rounded-full" />
          <div className="flex min-w-0 flex-1 flex-col gap-2">
            <SkeletonLine className="w-1/3" />
            <SkeletonLine className="w-2/3" />
          </div>
        </div>
      ))}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Error                                                                       */
/* -------------------------------------------------------------------------- */

/**
 * The backend writes `hint` to tell the reader exactly what to do next, so it
 * is rendered verbatim whenever it is present.
 */
export function ErrorState({
  error,
  onRetry,
  title,
  compact = false,
}: {
  error: ApiError;
  onRetry?: () => void;
  title?: string;
  compact?: boolean;
}) {
  return (
    <div
      role="alert"
      className={cx(
        "rounded-xl border border-danger-soft bg-danger-soft",
        compact ? "p-3" : "p-4 sm:p-5",
      )}
    >
      <p className="text-sm font-semibold text-danger break-anywhere">
        {title ?? error.message}
      </p>
      {title ? <p className="mt-1 text-sm text-ink/85 break-anywhere">{error.message}</p> : null}
      {error.hint ? (
        <p className="mt-2 text-sm text-ink/85 break-anywhere">{error.hint}</p>
      ) : null}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        {onRetry ? (
          <Button size="sm" variant="secondary" onClick={onRetry}>
            Try again
          </Button>
        ) : null}
        {error.provider ? (
          <span className="text-xs text-muted">Provider: {error.provider}</span>
        ) : null}
        {error.status ? <span className="text-xs text-muted">Status {error.status}</span> : null}
      </div>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Empty                                                                       */
/* -------------------------------------------------------------------------- */

export function EmptyState({
  title,
  children,
  action,
  icon,
}: {
  title: string;
  children?: ReactNode;
  action?: ReactNode;
  icon?: ReactNode;
}) {
  return (
    <div className="rounded-xl border border-dashed border-line-strong bg-surface px-4 py-10 text-center sm:px-8">
      {icon ? <div className="mb-3 flex justify-center text-faint">{icon}</div> : null}
      <h3 className="text-base font-semibold">{title}</h3>
      {children ? (
        <div className="mx-auto mt-2 max-w-prose text-sm text-muted">{children}</div>
      ) : null}
      {action ? <div className="mt-5 flex flex-wrap justify-center gap-2">{action}</div> : null}
    </div>
  );
}
