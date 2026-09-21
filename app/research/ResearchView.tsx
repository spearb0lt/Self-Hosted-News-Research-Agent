"use client";

import { useRouter } from "next/navigation";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { ProviderPicker, useProviderSelection } from "@/components/ProviderPicker";
import { ErrorState } from "@/components/states";
import { Badge, Button, Card, SectionHeading, TextInput, cx } from "@/components/ui";
import { ApiError, api, toApiError } from "@/lib/api";
import { relativeTime } from "@/lib/format";
import { useAsync } from "@/lib/hooks";
import { parseMarkdown, type Block, type InlineToken } from "@/lib/markdown";
import type {
  ResearchArticle,
  ResearchBackend,
  ResearchMode,
  ResearchResponse,
} from "@/lib/types";

const RESULT_LIMIT = 25;
/** Long enough to read, short enough not to linger after the jump. */
const HIGHLIGHT_MS = 2400;

/**
 * A topic search that does not touch a workspace.
 *
 * Raw mode is the default on purpose: it ranks and lists what the search
 * backends returned without calling a model at all, so it works with no key.
 */
export function ResearchView() {
  const router = useRouter();
  const { selection, setSelection, providers } = useProviderSelection();

  const [query, setQuery] = useState("");
  const [mode, setMode] = useState<ResearchMode>("raw");
  const [disabledBackends, setDisabledBackends] = useState<string[]>([]);

  const [result, setResult] = useState<ResearchResponse | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [running, setRunning] = useState(false);
  const [startedAt, setStartedAt] = useState(0);
  const inFlight = useRef<AbortController | null>(null);

  const loadBackends = useCallback((signal: AbortSignal) => api.researchBackends(signal), []);
  const backendState = useAsync(loadBackends, []);
  const backends = useMemo(() => backendState.data?.backends ?? [], [backendState.data]);

  const canReport = providers.length > 0;

  // A key pasted after the page loaded can make report mode possible, but the
  // reverse leaves the mode pointing at something the backend will refuse.
  useEffect(() => {
    if (!canReport) setMode("raw");
  }, [canReport]);

  useEffect(() => () => inFlight.current?.abort(), []);

  const chosenBackends = useMemo(
    () =>
      backends
        .filter((entry) => entry.available && !disabledBackends.includes(entry.kind))
        .map((entry) => entry.kind),
    [backends, disabledBackends],
  );

  const search = useCallback(() => {
    const trimmed = query.trim();
    if (!trimmed || running) return;

    inFlight.current?.abort();
    const controller = new AbortController();
    inFlight.current = controller;

    setRunning(true);
    setError(null);
    setResult(null);
    setStartedAt(Date.now());

    api
      .research(
        {
          query: trimmed,
          mode,
          backends: chosenBackends.length > 0 ? chosenBackends : null,
          limit: RESULT_LIMIT,
          extract: true,
          provider: mode === "report" ? selection.provider || null : null,
          model: mode === "report" ? selection.model || null : null,
        },
        controller.signal,
      )
      .then((response) => setResult(response))
      .catch((cause: unknown) => {
        if (cause instanceof DOMException && cause.name === "AbortError") return;
        setError(toApiError(cause));
      })
      .finally(() => {
        if (inFlight.current === controller) {
          inFlight.current = null;
          setRunning(false);
        }
      });
  }, [query, running, mode, chosenBackends, selection]);

  const cancel = useCallback(() => {
    inFlight.current?.abort();
    inFlight.current = null;
    setRunning(false);
  }, []);

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">Research a topic</h1>
        <p className="mt-1.5 max-w-prose text-sm text-muted">
          Search the news for one thing, right now, without creating a workspace first. Keep
          what you find as a daily beat if it is worth following.
        </p>
      </div>

      <Card className="p-4 sm:p-6">
        <div className="flex flex-col gap-2 sm:flex-row">
          <TextInput
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                search();
              }
            }}
            placeholder="Mumbai coastal road, chip export controls, monsoon damage"
            aria-label="What to research"
            className="sm:flex-1 sm:text-base"
            autoComplete="off"
          />
          <Button variant="primary" onClick={search} disabled={!query.trim() || running}>
            {running ? "Searching" : "Search"}
          </Button>
        </div>

        <fieldset className="mt-5">
          <legend className="text-sm font-medium">What you get back</legend>
          <div className="mt-2 grid gap-2 sm:grid-cols-2">
            <ModeCard
              active={mode === "raw"}
              onSelect={() => setMode("raw")}
              title="Just the articles"
              description="Ranked, deduplicated and numbered. No AI provider, no key, nothing sent to a model."
            />
            <ModeCard
              active={mode === "report"}
              onSelect={() => setMode("report")}
              disabled={!canReport}
              title="Written brief"
              description={
                canReport
                  ? "A short brief over the top results, citing each claim, above the same list."
                  : "Needs an AI provider, and none is usable yet."
              }
              footer={
                canReport ? null : (
                  <Link
                    href="/settings"
                    className="text-xs font-medium text-accent underline underline-offset-2"
                  >
                    Add a key in Settings
                  </Link>
                )
              }
            />
          </div>
        </fieldset>

        {mode === "report" && canReport ? (
          <div className="mt-4 rounded-lg border border-line bg-raised p-3">
            <ProviderPicker selection={selection} onChange={setSelection} providers={providers} />
          </div>
        ) : null}

        <BackendPicker
          backends={backends}
          disabledBackends={disabledBackends}
          onToggle={(kind, on) =>
            setDisabledBackends((current) =>
              on ? current.filter((entry) => entry !== kind) : [...current, kind],
            )
          }
          loading={backendState.initialising}
          error={backendState.error}
          onRetry={backendState.reload}
        />
      </Card>

      {running ? (
        <ResearchProgress mode={mode} startedAt={startedAt} onCancel={cancel} />
      ) : null}

      {error ? <ErrorState error={error} title="The search did not finish" /> : null}

      {result ? (
        <ResearchResult
          result={result}
          onSaved={(slug) => router.push(`/w/${encodeURIComponent(slug)}`)}
        />
      ) : null}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Controls                                                                    */
/* -------------------------------------------------------------------------- */

function ModeCard({
  active,
  disabled = false,
  onSelect,
  title,
  description,
  footer,
}: {
  active: boolean;
  disabled?: boolean;
  onSelect: () => void;
  title: string;
  description: string;
  footer?: ReactNode;
}) {
  return (
    <div
      className={cx(
        "rounded-lg border p-3 transition-colors",
        active ? "border-accent bg-accent-soft" : "border-line",
        disabled && "opacity-70",
      )}
    >
      <label className={cx("flex items-start gap-2.5", disabled ? "cursor-not-allowed" : "cursor-pointer")}>
        <input
          type="radio"
          name="research-mode"
          checked={active}
          disabled={disabled}
          onChange={onSelect}
          className="mt-0.5 size-4 shrink-0 accent-[var(--accent)]"
        />
        <span className="min-w-0">
          <span className="block text-sm font-medium">{title}</span>
          <span className="block text-xs text-muted">{description}</span>
        </span>
      </label>
      {footer ? <div className="mt-2 pl-7">{footer}</div> : null}
    </div>
  );
}

function BackendPicker({
  backends,
  disabledBackends,
  onToggle,
  loading,
  error,
  onRetry,
}: {
  backends: ResearchBackend[];
  disabledBackends: string[];
  onToggle: (kind: string, on: boolean) => void;
  loading: boolean;
  error: ApiError | null;
  onRetry: () => void;
}) {
  const [open, setOpen] = useState(false);
  const usable = backends.filter((entry) => entry.available);
  const locked = backends.filter((entry) => !entry.available);

  if (error) {
    return (
      <div className="mt-4">
        <ErrorState error={error} compact onRetry={onRetry} title="Could not list the search backends" />
      </div>
    );
  }

  return (
    <div className="mt-4 border-t border-line pt-4">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="rounded-md text-sm font-medium text-accent underline underline-offset-2"
      >
        {open ? "Hide search backends" : "Search backends"}
      </button>
      <span className="ml-2 text-xs text-muted">
        {loading
          ? "loading"
          : `${usable.length} usable, ${locked.length} waiting on a key`}
      </span>

      {open ? (
        <div className="mt-3 grid gap-2 sm:grid-cols-2">
          {usable.map((entry) => (
            <label key={entry.kind} className="flex cursor-pointer items-start gap-2.5 rounded-lg border border-line p-2.5">
              <input
                type="checkbox"
                checked={!disabledBackends.includes(entry.kind)}
                onChange={(event) => onToggle(entry.kind, event.target.checked)}
                className="mt-0.5 size-4 shrink-0 accent-[var(--accent)]"
              />
              <span className="min-w-0">
                <span className="block text-sm font-medium">{entry.label}</span>
                <span className="block text-xs text-muted">No key needed.</span>
              </span>
            </label>
          ))}
          {locked.map((entry) => (
            <div
              key={entry.kind}
              className="flex items-start gap-2.5 rounded-lg border border-line p-2.5 opacity-60"
            >
              <input
                type="checkbox"
                checked={false}
                disabled
                readOnly
                aria-label={entry.label}
                className="mt-0.5 size-4 shrink-0"
              />
              <span className="min-w-0">
                <span className="block text-sm font-medium text-muted">{entry.label}</span>
                <span className="block text-xs text-faint break-anywhere">{entry.reason}</span>
              </span>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Progress                                                                    */
/* -------------------------------------------------------------------------- */

interface Stage {
  after: number;
  label: string;
}

const RAW_STAGES: Stage[] = [
  { after: 0, label: "Asking the search backends" },
  { after: 3, label: "Reading the articles that answered" },
  { after: 9, label: "Merging stories that are the same" },
  { after: 15, label: "Ranking what is left against your query" },
];

const REPORT_STAGES: Stage[] = [...RAW_STAGES, { after: 22, label: "Writing the brief" }];

/**
 * The search endpoint answers once, at the end, so there is nothing to poll.
 * Rather than a bare spinner this narrates the stages in the order they run
 * and counts the seconds, and it says plainly that the bar is an estimate.
 */
function ResearchProgress({
  mode,
  startedAt,
  onCancel,
}: {
  mode: ResearchMode;
  startedAt: number;
  onCancel: () => void;
}) {
  const [seconds, setSeconds] = useState(0);

  useEffect(() => {
    setSeconds(0);
    const timer = window.setInterval(
      () => setSeconds(Math.max(0, Math.round((Date.now() - startedAt) / 1000))),
      500,
    );
    return () => window.clearInterval(timer);
  }, [startedAt]);

  const stages = mode === "report" ? REPORT_STAGES : RAW_STAGES;
  const expected = mode === "report" ? 45 : 20;
  const reached = stages.filter((stage) => seconds >= stage.after);
  const current = reached[reached.length - 1] ?? stages[0];
  const percent = Math.min(95, Math.round((seconds / expected) * 100));

  return (
    <Card className="p-4 sm:p-5">
      <div className="flex flex-wrap items-center gap-3">
        <span className="text-sm font-medium" aria-live="polite">
          {current.label}
        </span>
        <span className="text-xs text-muted">{seconds}s</span>
        <Button size="sm" variant="ghost" className="ml-auto" onClick={onCancel}>
          Cancel
        </Button>
      </div>

      <div
        className="mt-3 h-2 w-full overflow-hidden rounded-full bg-raised"
        role="progressbar"
        aria-label="Search progress"
        aria-valuetext={current.label}
      >
        <div
          className="h-full rounded-full bg-accent transition-[width] duration-500"
          style={{ width: `${percent}%` }}
        />
      </div>

      <ol className="mt-3 flex flex-col gap-1">
        {stages.map((stage) => {
          const done = stage !== current && seconds >= stage.after;
          const active = stage === current;
          return (
            <li
              key={stage.label}
              className={cx(
                "text-xs",
                active ? "font-medium text-ink" : done ? "text-muted" : "text-faint",
              )}
            >
              {done ? "done " : active ? "now " : "next "}
              {stage.label.toLowerCase()}
            </li>
          );
        })}
      </ol>

      <p className="mt-3 text-xs text-faint">
        A search usually takes 10 to 30 seconds. The backend answers once, at the end, so the bar
        above is an estimate rather than a measurement.
      </p>
    </Card>
  );
}

/* -------------------------------------------------------------------------- */
/* Results                                                                     */
/* -------------------------------------------------------------------------- */

function articleDomId(n: number): string {
  return `research-article-${n}`;
}

function ResearchResult({
  result,
  onSaved,
}: {
  result: ResearchResponse;
  onSaved: (slug: string) => void;
}) {
  const [highlighted, setHighlighted] = useState<number | null>(null);
  const [jumps, setJumps] = useState(0);

  const known = useMemo(
    () => new Set(result.articles.map((article) => article.n)),
    [result.articles],
  );

  const jumpTo = useCallback((n: number) => {
    const target = document.getElementById(articleDomId(n));
    if (!target) return;
    target.scrollIntoView({ behavior: "smooth", block: "center" });
    setHighlighted(n);
    setJumps((value) => value + 1);
  }, []);

  useEffect(() => {
    if (highlighted === null) return;
    const timer = window.setTimeout(() => setHighlighted(null), HIGHLIGHT_MS);
    return () => window.clearTimeout(timer);
    // `jumps` is in here so pressing the same marker twice restarts the fade.
  }, [highlighted, jumps]);

  return (
    <div className="flex flex-col gap-5">
      <ResultSummary result={result} onSaved={onSaved} />

      {result.report ? (
        <Card className="p-4 sm:p-6">
          <SectionHeading
            title="Brief"
            description="Written by a model from the articles below. Press a citation to check it against the source it came from."
          />
          <Brief markdown={result.report} known={known} onCitation={jumpTo} />
        </Card>
      ) : null}

      {result.articles.length === 0 ? (
        <Card className="p-6 text-center">
          <p className="text-sm font-medium">Nothing came back for that.</p>
          <p className="mt-1 text-sm text-muted">
            Try fewer words, or a name and a place rather than a question.
          </p>
        </Card>
      ) : (
        <ol className="flex flex-col gap-3">
          {result.articles.map((article) => (
            <ResultRow
              key={article.n}
              article={article}
              highlighted={highlighted === article.n}
            />
          ))}
        </ol>
      )}
    </div>
  );
}

function ResultSummary({
  result,
  onSaved,
}: {
  result: ResearchResponse;
  onSaved: (slug: string) => void;
}) {
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<ApiError | null>(null);

  const save = () => {
    if (saving) return;
    setSaving(true);
    setSaveError(null);
    api
      .saveResearch({ query: result.query, backends: result.backends_used })
      .then((workspace) => onSaved(workspace.slug))
      .catch((cause: unknown) => {
        setSaveError(toApiError(cause));
        setSaving(false);
      });
  };

  return (
    <Card className="p-4 sm:p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-base font-semibold tracking-tight break-anywhere">
            {result.query}
          </h2>
          <p className="mt-1 text-sm text-muted">
            {result.total_found} found, {result.duplicates_removed} merged as duplicates,{" "}
            {result.articles.length} listed.
          </p>
        </div>
        <Button variant="primary" onClick={save} loading={saving}>
          Save as workspace
        </Button>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-1.5">
        <span className="text-xs text-muted">Searched</span>
        {result.backends_used.map((kind) => (
          <Badge key={kind} tone="ok">
            {kind}
          </Badge>
        ))}
        {result.backends_used.length === 0 ? (
          <span className="text-xs text-faint">nothing answered</span>
        ) : null}
      </div>

      {result.backends_failed.length > 0 ? (
        <details className="mt-3 rounded-lg border border-line bg-raised p-3">
          <summary className="cursor-pointer text-sm font-medium">
            {result.backends_failed.length} backend
            {result.backends_failed.length === 1 ? "" : "s"} did not answer
          </summary>
          <ul className="mt-2 flex flex-col gap-2">
            {result.backends_failed.map((failure, index) => (
              <li key={`${failure.backend}-${index}`} className="text-sm">
                <span className="font-medium">{failure.backend}</span>
                {/* Verbatim: the message names the key that would widen the search. */}
                <span className="mt-0.5 block text-xs text-muted break-anywhere">
                  {failure.error}
                </span>
              </li>
            ))}
          </ul>
        </details>
      ) : null}

      <p className="mt-3 text-xs text-faint">
        Saving builds a workspace from the outlets that actually answered, and runs this search
        every day from then on.
      </p>

      {saveError ? (
        <div className="mt-3">
          <ErrorState error={saveError} compact />
        </div>
      ) : null}
    </Card>
  );
}

function ResultRow({ article, highlighted }: { article: ResearchArticle; highlighted: boolean }) {
  return (
    <Card
      as="li"
      id={articleDomId(article.n)}
      className={cx(
        "scroll-mt-20 p-4 transition-colors duration-300 sm:p-5",
        highlighted && "border-accent bg-accent-soft",
      )}
    >
      <div className="flex gap-3">
        <span
          aria-hidden="true"
          className="mt-0.5 w-7 shrink-0 text-right text-sm font-semibold tabular-nums text-faint"
        >
          {article.n}
        </span>
        <div className="min-w-0 flex-1">
          <h3 className="text-base font-semibold break-anywhere">
            <a
              href={article.url}
              target="_blank"
              rel="noopener noreferrer"
              className="rounded-sm hover:text-accent"
            >
              <span className="sr-only">Result {article.n}: </span>
              {article.title || article.url}
            </a>
          </h3>

          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted">
            <span className="font-medium break-anywhere">{article.domain || "Unknown outlet"}</span>
            {article.published_at ? <span>{relativeTime(article.published_at)}</span> : null}
            <span className="text-faint" title="How closely this matched your query">
              relevance {article.relevance.toFixed(2)}
            </span>
          </div>

          {article.summary ? (
            <p className="mt-2 text-sm leading-relaxed text-ink/90 break-anywhere">
              {article.summary}
            </p>
          ) : null}
        </div>
      </div>
    </Card>
  );
}

/* -------------------------------------------------------------------------- */
/* Brief                                                                       */
/* -------------------------------------------------------------------------- */

function Brief({
  markdown,
  known,
  onCitation,
}: {
  markdown: string;
  known: Set<number>;
  onCitation: (n: number) => void;
}) {
  const blocks = useMemo<Block[]>(() => parseMarkdown(markdown), [markdown]);

  const render = (tokens: InlineToken[]) =>
    tokens.map((token, index) => (
      <Inline key={index} token={token} known={known} onCitation={onCitation} />
    ));

  return (
    <div className="prose-digest text-sm">
      {blocks.map((block, index) => {
        switch (block.kind) {
          case "heading": {
            if (block.level === 1) return <h1 key={index}>{render(block.content)}</h1>;
            if (block.level === 2) return <h2 key={index}>{render(block.content)}</h2>;
            return <h3 key={index}>{render(block.content)}</h3>;
          }
          case "list":
            return block.ordered ? (
              <ol key={index}>
                {block.items.map((item, itemIndex) => (
                  <li key={itemIndex}>{render(item)}</li>
                ))}
              </ol>
            ) : (
              <ul key={index}>
                {block.items.map((item, itemIndex) => (
                  <li key={itemIndex}>{render(item)}</li>
                ))}
              </ul>
            );
          case "quote":
            return <blockquote key={index}>{render(block.content)}</blockquote>;
          case "rule":
            return <hr key={index} />;
          default:
            return <p key={index}>{render(block.content)}</p>;
        }
      })}
    </div>
  );
}

function Inline({
  token,
  known,
  onCitation,
}: {
  token: InlineToken;
  known: Set<number>;
  onCitation: (n: number) => void;
}) {
  switch (token.kind) {
    case "strong":
      return <strong>{token.value}</strong>;
    case "emphasis":
      return <em>{token.value}</em>;
    case "code":
      return <code>{token.value}</code>;
    case "link":
      return (
        <a href={token.href} target="_blank" rel="noopener noreferrer">
          {token.value}
        </a>
      );
    case "citation":
      return (
        <span className="whitespace-nowrap">
          [
          {token.numbers.map((n, index) => (
            <span key={`${n}-${index}`}>
              {index > 0 ? ", " : null}
              {known.has(n) ? (
                <button
                  type="button"
                  onClick={() => onCitation(n)}
                  className="rounded-sm font-medium text-accent underline underline-offset-2 tabular-nums"
                  title={`Jump to result ${n}`}
                >
                  {n}
                </button>
              ) : (
                <span className="tabular-nums text-faint">{n}</span>
              )}
            </span>
          ))}
          ]
        </span>
      );
    default:
      return <>{token.value}</>;
  }
}
