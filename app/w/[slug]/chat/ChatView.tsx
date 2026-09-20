"use client";

import { useEffect, useRef, useState } from "react";

import { ProviderPicker, useProviderSelection } from "@/components/ProviderPicker";
import { ErrorState } from "@/components/states";
import { Badge, Button, Card, Spinner, TextArea, cx } from "@/components/ui";
import { ApiError, api, toApiError } from "@/lib/api";
import { truncate } from "@/lib/format";
import type { Citation } from "@/lib/types";

interface Turn {
  id: string;
  question: string;
  answer: string | null;
  citations: Citation[];
  error: ApiError | null;
}

const SUGGESTIONS = [
  "What changed this week?",
  "Which story had the most coverage?",
  "Summarise the arguments on both sides.",
];

/**
 * Questions are answered from the workspace archive, and the answer arrives
 * with the articles it leaned on. Those are rendered as chips under the answer
 * so a claim can be checked in one click.
 */
export function ChatView({ slug }: { slug: string }) {
  const { selection, setSelection, providers } = useProviderSelection();
  const [question, setQuestion] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);
  const [showOptions, setShowOptions] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [turns, busy]);

  const ask = (text: string) => {
    const trimmed = text.trim();
    if (!trimmed || busy) return;

    const id = `${Date.now()}-${turns.length}`;
    setTurns((current) => [
      ...current,
      { id, question: trimmed, answer: null, citations: [], error: null },
    ]);
    setQuestion("");
    setBusy(true);

    api
      .chat(slug, {
        question: trimmed,
        provider: selection.provider || undefined,
        model: selection.model || undefined,
      })
      .then((response) => {
        setTurns((current) =>
          current.map((turn) =>
            turn.id === id
              ? { ...turn, answer: response.answer, citations: response.citations ?? [] }
              : turn,
          ),
        );
      })
      .catch((cause: unknown) => {
        const apiError = toApiError(cause);
        setTurns((current) =>
          current.map((turn) => (turn.id === id ? { ...turn, error: apiError } : turn)),
        );
      })
      .finally(() => setBusy(false));
  };

  return (
    <div className="flex flex-col gap-5">
      <Card className="p-4 sm:p-5">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="min-w-0">
            <h2 className="text-base font-semibold tracking-tight">Ask the archive</h2>
            <p className="mt-1 text-sm text-muted">
              Answers are drawn from articles this workspace has collected, with the sources
              shown underneath.
            </p>
          </div>
          <Button size="sm" variant="ghost" onClick={() => setShowOptions((value) => !value)} aria-expanded={showOptions}>
            {showOptions ? "Hide model" : "Model"}
          </Button>
        </div>
        {showOptions ? (
          <div className="mt-4">
            <ProviderPicker selection={selection} onChange={setSelection} providers={providers} />
          </div>
        ) : null}
      </Card>

      <div className="flex flex-col gap-4" aria-live="polite">
        {turns.length === 0 ? (
          <Card className="p-4 sm:p-5">
            <p className="text-sm text-muted">Nothing asked yet. A few starting points:</p>
            <div className="mt-3 flex flex-wrap gap-2">
              {SUGGESTIONS.map((suggestion) => (
                <button
                  key={suggestion}
                  type="button"
                  onClick={() => ask(suggestion)}
                  disabled={busy}
                  className="rounded-full border border-line bg-surface px-3 py-1.5 text-sm text-muted transition-colors hover:text-ink disabled:opacity-55"
                >
                  {suggestion}
                </button>
              ))}
            </div>
          </Card>
        ) : null}

        {turns.map((turn) => (
          <div key={turn.id} className="flex flex-col gap-2">
            <div className="flex justify-end">
              <p className="max-w-[90%] rounded-2xl rounded-br-sm bg-accent px-4 py-2.5 text-sm text-accent-ink break-anywhere">
                {turn.question}
              </p>
            </div>

            {turn.error ? (
              <ErrorState error={turn.error} title="That question could not be answered" />
            ) : turn.answer === null ? (
              <Card className="flex items-center gap-2 p-4 text-sm text-muted">
                <Spinner />
                Reading the archive
              </Card>
            ) : (
              <Card className="p-4 sm:p-5">
                <div className="text-sm leading-relaxed whitespace-pre-wrap break-anywhere">
                  {turn.answer}
                </div>
                {turn.citations.length > 0 ? (
                  <div className="mt-4 border-t border-line pt-3">
                    <p className="text-xs font-medium tracking-wide text-muted uppercase">
                      Sources
                    </p>
                    <ul className="mt-2 flex flex-wrap gap-2">
                      {turn.citations.map((citation, index) => (
                        <li key={`${String(citation.article_id)}-${index}`}>
                          <CitationChip citation={citation} index={index + 1} />
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : null}
              </Card>
            )}
          </div>
        ))}
        <div ref={endRef} />
      </div>

      <form
        className="sticky bottom-0 flex flex-col gap-2 bg-canvas pt-2 pb-4"
        onSubmit={(event) => {
          event.preventDefault();
          ask(question);
        }}
      >
        <TextArea
          value={question}
          rows={2}
          onChange={(event) => setQuestion(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
              event.preventDefault();
              ask(question);
            }
          }}
          placeholder="Ask anything about what this workspace has collected"
          aria-label="Your question"
        />
        <div className="flex flex-wrap items-center gap-2">
          <Button type="submit" variant="primary" loading={busy} disabled={!question.trim()}>
            Ask
          </Button>
          <span className="text-xs text-faint">Control and Enter also sends.</span>
        </div>
      </form>
    </div>
  );
}

function CitationChip({ citation, index }: { citation: Citation; index: number }) {
  const [open, setOpen] = useState(false);
  const label = citation.title || citation.domain || citation.url;

  return (
    <div className="max-w-full">
      <div className="flex items-stretch overflow-hidden rounded-full border border-line bg-surface">
        <a
          href={citation.url}
          target="_blank"
          rel="noopener noreferrer"
          className="flex min-w-0 items-center gap-1.5 px-3 py-1.5 text-xs font-medium hover:bg-raised"
          title={citation.url}
        >
          <Badge tone="accent">{index}</Badge>
          <span className="truncate max-w-[14rem]">{truncate(label, 60)}</span>
          {citation.domain ? (
            <span className="shrink-0 text-faint">{citation.domain}</span>
          ) : null}
        </a>
        {citation.snippet ? (
          <button
            type="button"
            onClick={() => setOpen((value) => !value)}
            aria-expanded={open}
            className={cx(
              "border-l border-line px-2 text-xs text-muted transition-colors hover:bg-raised hover:text-ink",
            )}
          >
            {open ? "Hide" : "Quote"}
          </button>
        ) : null}
      </div>
      {open && citation.snippet ? (
        <p className="mt-1.5 rounded-lg bg-raised px-3 py-2 text-xs text-muted break-anywhere">
          {citation.snippet}
        </p>
      ) : null}
    </div>
  );
}
