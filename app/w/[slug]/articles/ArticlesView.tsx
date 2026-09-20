"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { EmptyState, ErrorState, SkeletonCards } from "@/components/states";
import { Badge, Button, Card, TextInput, Toggle, cx } from "@/components/ui";
import { ApiError, api, toApiError } from "@/lib/api";
import { absoluteDateTime, compactNumber, hostOf, themeLabel } from "@/lib/format";
import { useAsync, useDebounced } from "@/lib/hooks";
import type { Article, ScoredArticle, Theme } from "@/lib/types";

const PAGE_SIZE = 25;

export function ArticlesView({ slug }: { slug: string }) {
  const [query, setQuery] = useState("");
  const [theme, setTheme] = useState("");
  const [semantic, setSemantic] = useState(false);
  const debouncedQuery = useDebounced(query, 350);

  // Themes come from the workspace, so the filter lists the taxonomy even when
  // the current page of articles happens not to use all of it.
  const loadWorkspace = useCallback(
    (signal: AbortSignal) => api.getWorkspace(slug, signal),
    [slug],
  );
  const workspace = useAsync(loadWorkspace, [slug]);
  const themes = workspace.data?.themes ?? [];

  const semanticActive = semantic && debouncedQuery.trim().length > 0;

  const loadArticles = useCallback(
    (signal: AbortSignal) => {
      if (semanticActive) {
        return api
          .search(slug, debouncedQuery.trim(), PAGE_SIZE, signal)
          .then((response) => ({
            articles: response.results as Article[],
            total: response.results.length,
          }));
      }
      return api.articles(
        slug,
        {
          theme: theme || undefined,
          q: debouncedQuery.trim() || undefined,
          limit: PAGE_SIZE,
          offset: 0,
        },
        signal,
      );
    },
    [slug, theme, debouncedQuery, semanticActive],
  );

  const { data, error, initialising, loading, reload } = useAsync(loadArticles, [
    slug,
    theme,
    debouncedQuery,
    semanticActive,
  ]);

  const [extra, setExtra] = useState<Article[]>([]);
  const [loadingMore, setLoadingMore] = useState(false);
  const [moreError, setMoreError] = useState<ApiError | null>(null);
  const firstRender = useRef(true);

  // Any change of filter throws away the pages loaded on top of the first one.
  useEffect(() => {
    if (firstRender.current) {
      firstRender.current = false;
      return;
    }
    setExtra([]);
    setMoreError(null);
  }, [slug, theme, debouncedQuery, semanticActive]);

  const articles = useMemo(() => [...(data?.articles ?? []), ...extra], [data, extra]);
  const total = data?.total ?? articles.length;
  const canLoadMore = !semanticActive && articles.length < total;

  const loadMore = () => {
    if (loadingMore) return;
    setLoadingMore(true);
    setMoreError(null);
    api
      .articles(slug, {
        theme: theme || undefined,
        q: debouncedQuery.trim() || undefined,
        limit: PAGE_SIZE,
        offset: articles.length,
      })
      .then((response) => setExtra((current) => [...current, ...response.articles]))
      .catch((cause: unknown) => setMoreError(toApiError(cause)))
      .finally(() => setLoadingMore(false));
  };

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-col gap-3">
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
          <TextInput
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search headlines and summaries"
            aria-label="Search articles"
            className="sm:flex-1"
            autoComplete="off"
          />
          <Toggle
            checked={semantic}
            onChange={setSemantic}
            label="Search by meaning"
            description="Uses the embedding index instead of matching words."
          />
        </div>

        {themes.length > 0 ? (
          <div
            role="group"
            aria-label="Filter by theme"
            className="-mx-3 flex gap-1.5 overflow-x-auto px-3 pb-1 sm:mx-0 sm:flex-wrap sm:px-0"
          >
            <FilterChip label="All themes" active={theme === ""} onClick={() => setTheme("")} />
            {themes.map((entry) => (
              <FilterChip
                key={entry.id}
                label={entry.label}
                title={entry.hint || undefined}
                active={theme === entry.id}
                onClick={() => setTheme(entry.id)}
                disabled={semanticActive}
              />
            ))}
          </div>
        ) : null}

        {semanticActive && theme ? (
          <p className="text-xs text-muted">
            Search by meaning looks across every theme, so the theme filter is ignored while it is
            on.
          </p>
        ) : null}
      </div>

      <div className="flex items-center gap-3 text-sm text-muted">
        <span aria-live="polite">
          {initialising
            ? "Loading articles"
            : semanticActive
              ? `${compactNumber(articles.length)} best matches`
              : `${compactNumber(total)} ${total === 1 ? "article" : "articles"}`}
        </span>
        {loading && !initialising ? <span className="text-xs">updating</span> : null}
      </div>

      {initialising ? (
        <SkeletonCards count={5} lines={2} />
      ) : error ? (
        <ErrorState error={error} onRetry={reload} title="Could not load articles" />
      ) : articles.length === 0 ? (
        <EmptyState title={query || theme ? "Nothing matched" : "No articles yet"}>
          {query || theme ? (
            <p>Try a shorter search, another theme, or turn search by meaning on or off.</p>
          ) : (
            <p>Articles appear here after the first run collects them.</p>
          )}
        </EmptyState>
      ) : (
        <>
          <ul className="flex flex-col gap-3">
            {articles.map((article) => (
              <ArticleCard key={String(article.id)} article={article} themes={themes} />
            ))}
          </ul>

          {moreError ? <ErrorState error={moreError} compact onRetry={loadMore} /> : null}

          {canLoadMore ? (
            <div className="flex justify-center">
              <Button variant="secondary" onClick={loadMore} loading={loadingMore}>
                Load more
              </Button>
            </div>
          ) : null}
        </>
      )}
    </div>
  );
}

function FilterChip({
  label,
  active,
  onClick,
  disabled,
  title,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
  disabled?: boolean;
  title?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      disabled={disabled}
      title={title}
      className={cx(
        "shrink-0 rounded-full border px-3 py-1 text-sm font-medium transition-colors disabled:opacity-50",
        active
          ? "border-transparent bg-accent text-accent-ink"
          : "border-line bg-surface text-muted hover:text-ink",
      )}
    >
      {label}
    </button>
  );
}

function hasScore(article: Article): article is ScoredArticle {
  return typeof (article as ScoredArticle).score === "number";
}

function ArticleCard({ article, themes }: { article: Article; themes: Theme[] }) {
  const label = themeLabel(themes, article.theme);
  return (
    <Card as="li" className="p-4 sm:p-5">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <h3 className="min-w-0 flex-1 text-base font-semibold break-anywhere">
          <a
            href={article.url}
            target="_blank"
            rel="noopener noreferrer"
            className="rounded-sm hover:text-accent"
          >
            {article.title || article.url}
          </a>
        </h3>
        <div className="flex shrink-0 flex-wrap gap-1.5">
          {label ? <Badge tone="accent">{label}</Badge> : null}
          {hasScore(article) ? (
            <Badge tone="neutral" title="Similarity to your search">
              {article.score.toFixed(2)}
            </Badge>
          ) : null}
        </div>
      </div>

      {article.summary ? (
        <p className="mt-2 text-sm leading-relaxed text-ink/90 break-anywhere">{article.summary}</p>
      ) : null}

      <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted">
        <span className="break-anywhere">{article.domain || hostOf(article.url)}</span>
        {article.author ? <span className="break-anywhere">{article.author}</span> : null}
        {article.published_at ? <span>{absoluteDateTime(article.published_at)}</span> : null}
        {article.word_count ? <span>{compactNumber(article.word_count)} words</span> : null}
        {article.sentiment ? <span>{article.sentiment}</span> : null}
      </div>
    </Card>
  );
}
