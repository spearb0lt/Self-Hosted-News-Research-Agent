"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { RunBar } from "@/components/RunBar";
import { EmptyState, ErrorState, SkeletonRows } from "@/components/states";
import { Button, Card, Toggle } from "@/components/ui";
import { ApiError, api, toApiError } from "@/lib/api";
import { absoluteDateTime, compactNumber, hostOf, relativeTime } from "@/lib/format";
import { useAsync } from "@/lib/hooks";
import type { Article } from "@/lib/types";

const PAGE_SIZE = 50;

/** An article with the position it holds in the whole, newest first, list. */
interface NumberedArticle {
  n: number;
  article: Article;
}

/**
 * Every article as it arrived, newest first, with nothing grouped or written.
 *
 * This is the only readable view of a workspace whose runs make no AI calls,
 * and it is the one to check a digest against, so the numbering follows the
 * server's own ordering: row numbers come from the offset that fetched them
 * rather than from where a row happens to sit on screen.
 */
export function RawView({ slug }: { slug: string }) {
  const [includeDuplicates, setIncludeDuplicates] = useState(false);

  const load = useCallback(
    (signal: AbortSignal) =>
      api.articles(
        slug,
        {
          limit: PAGE_SIZE,
          offset: 0,
          include_duplicates: includeDuplicates || undefined,
        },
        signal,
      ),
    [slug, includeDuplicates],
  );
  const { data, error, initialising, reload } = useAsync(load, [slug, includeDuplicates]);

  const [extra, setExtra] = useState<NumberedArticle[]>([]);
  const [loadingMore, setLoadingMore] = useState(false);
  const [moreError, setMoreError] = useState<ApiError | null>(null);
  const firstRender = useRef(true);

  // A change of filter invalidates every page stacked on top of the first one.
  useEffect(() => {
    if (firstRender.current) {
      firstRender.current = false;
      return;
    }
    setExtra([]);
    setMoreError(null);
  }, [slug, includeDuplicates]);

  const rows = useMemo<NumberedArticle[]>(() => {
    const first = (data?.articles ?? []).map<NumberedArticle>((article, index) => ({
      n: index + 1,
      article,
    }));
    return [...first, ...extra];
  }, [data, extra]);

  const total = data?.total ?? rows.length;
  const canLoadMore = rows.length < total;

  const loadMore = () => {
    if (loadingMore) return;
    const offset = rows.length;
    setLoadingMore(true);
    setMoreError(null);
    api
      .articles(slug, {
        limit: PAGE_SIZE,
        offset,
        include_duplicates: includeDuplicates || undefined,
      })
      .then((response) =>
        setExtra((current) => [
          ...current,
          ...response.articles.map<NumberedArticle>((article, index) => ({
            n: offset + index + 1,
            article,
          })),
        ]),
      )
      .catch((cause: unknown) => setMoreError(toApiError(cause)))
      .finally(() => setLoadingMore(false));
  };

  return (
    <div className="flex flex-col gap-5">
      <RunBar slug={slug} onFinished={reload} />

      <div className="flex flex-wrap items-center justify-between gap-3">
        <span className="text-sm text-muted" aria-live="polite">
          {initialising
            ? "Loading articles"
            : `${compactNumber(total)} ${total === 1 ? "article" : "articles"}, newest first`}
        </span>
        <Toggle
          checked={includeDuplicates}
          onChange={setIncludeDuplicates}
          label="Include repeats of a story"
          description="Off, one article stands for each story. On, the ones the deduplicator merged into it are listed too."
        />
      </div>

      {initialising ? (
        <SkeletonRows count={8} />
      ) : error ? (
        <ErrorState error={error} onRetry={reload} title="Could not load articles" />
      ) : rows.length === 0 ? (
        <EmptyState title="No articles yet">
          <p>Articles appear here as soon as a run collects them.</p>
        </EmptyState>
      ) : (
        <>
          <Card>
            <ol className="flex flex-col">
              {rows.map(({ n, article }) => (
                <RawRow key={`${n}-${String(article.id)}`} n={n} article={article} />
              ))}
            </ol>
          </Card>

          {moreError ? <ErrorState error={moreError} compact onRetry={loadMore} /> : null}

          {canLoadMore ? (
            <div className="flex justify-center">
              <Button variant="secondary" onClick={loadMore} loading={loadingMore}>
                Load more
              </Button>
            </div>
          ) : (
            <p className="text-center text-xs text-faint">That is everything collected.</p>
          )}
        </>
      )}
    </div>
  );
}

function RawRow({ n, article }: { n: number; article: Article }) {
  return (
    <li className="flex gap-3 border-b border-line px-4 py-3 last:border-b-0 sm:px-5">
      <span
        aria-hidden="true"
        className="w-10 shrink-0 text-right text-sm tabular-nums text-faint"
      >
        {n}
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 text-xs text-muted">
          <span className="font-medium break-anywhere">
            {article.domain || hostOf(article.url)}
          </span>
          {article.published_at ? (
            <time dateTime={article.published_at} title={absoluteDateTime(article.published_at)}>
              {relativeTime(article.published_at)}
            </time>
          ) : (
            <span className="text-faint">no date</span>
          )}
        </div>
        <a
          href={article.url}
          target="_blank"
          rel="noopener noreferrer"
          className="mt-0.5 block rounded-sm text-sm font-medium break-anywhere hover:text-accent"
        >
          <span className="sr-only">Article {n}: </span>
          {article.title || article.url}
        </a>
      </div>
    </li>
  );
}
