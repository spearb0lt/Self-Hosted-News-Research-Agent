"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useConfig } from "@/components/ConfigProvider";
import { EmptyState, ErrorState, SkeletonRows } from "@/components/states";
import {
  Badge,
  Button,
  Card,
  Notice,
  Select,
  TextInput,
  Toggle,
  cx,
} from "@/components/ui";
import { ApiError, api, saveBlob, toApiError } from "@/lib/api";
import { plural, relativeTime } from "@/lib/format";
import { useAsync } from "@/lib/hooks";
import type { BadgeTone } from "@/components/ui";
import type {
  DiscoveredFeed,
  Id,
  JsonObject,
  OpmlImportResponse,
  Source,
  SourceKind,
  WorkspaceDetail,
} from "@/lib/types";

export function SourcesView({ slug }: { slug: string }) {
  const load = useCallback((signal: AbortSignal) => api.getWorkspace(slug, signal), [slug]);
  const { data, error, initialising, reload, setData } = useAsync<WorkspaceDetail>(load, [slug]);

  const sources = useMemo(() => data?.sources ?? [], [data]);

  const replaceSource = useCallback(
    (next: Source) => {
      if (!data) return;
      setData({
        ...data,
        sources: data.sources.map((entry) => (entry.id === next.id ? next : entry)),
      });
    },
    [data, setData],
  );

  const removeSource = useCallback(
    (id: Id) => {
      if (!data) return;
      const remaining = data.sources.filter((entry) => entry.id !== id);
      setData({
        ...data,
        sources: remaining,
        source_count: remaining.length,
      });
    },
    [data, setData],
  );

  return (
    <div className="flex flex-col gap-6">
      <DiscoverBox slug={slug} onAdded={reload} />
      <AddSourceForm slug={slug} onAdded={reload} />
      <OpmlCard slug={slug} onImported={reload} />

      <section>
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <h2 className="mr-auto text-base font-semibold tracking-tight">
            Sources
            {sources.length ? (
              <span className="ml-2 text-sm font-normal text-muted">{sources.length}</span>
            ) : null}
          </h2>
          <Button size="sm" variant="secondary" onClick={reload}>
            Refresh
          </Button>
        </div>

        {initialising ? (
          <SkeletonRows count={4} />
        ) : error ? (
          <ErrorState error={error} onRetry={reload} title="Could not load this workspace" />
        ) : sources.length === 0 ? (
          <EmptyState title="No sources yet">
            <p>
              Paste any website address in the box above and its feeds are found for you, or
              add a source by hand. Each source is checked on every run and its health is shown
              here.
            </p>
          </EmptyState>
        ) : (
          <ul className="flex flex-col gap-2">
            {sources.map((source) => (
              <SourceRow
                key={String(source.id)}
                source={source}
                onChanged={replaceSource}
                onRemoved={removeSource}
              />
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Discover                                                                    */
/* -------------------------------------------------------------------------- */

function DiscoverBox({ slug, onAdded }: { slug: string; onAdded: () => void }) {
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [feeds, setFeeds] = useState<DiscoveredFeed[] | null>(null);
  const [added, setAdded] = useState<Record<string, "adding" | "done" | "failed">>({});

  const run = () => {
    const target = url.trim();
    if (!target || busy) return;
    setBusy(true);
    setError(null);
    setFeeds(null);
    setAdded({});
    api
      .discover(slug, target)
      .then((result) => setFeeds(result.feeds))
      .catch((cause: unknown) => setError(toApiError(cause)))
      .finally(() => setBusy(false));
  };

  const add = (feed: DiscoveredFeed) => {
    setAdded((current) => ({ ...current, [feed.url]: "adding" }));
    api
      .addSource(slug, { kind: feed.kind, url: feed.url, name: feed.title || undefined })
      .then(() => {
        setAdded((current) => ({ ...current, [feed.url]: "done" }));
        onAdded();
      })
      .catch((cause: unknown) => {
        setAdded((current) => ({ ...current, [feed.url]: "failed" }));
        setError(toApiError(cause));
      });
  };

  return (
    <Card className="p-4 sm:p-5">
      <h2 className="text-base font-semibold tracking-tight">Paste any website address</h2>
      <p className="mt-1 text-sm text-muted">
        Give it a publication&apos;s home page and it looks for feeds you can subscribe to.
      </p>
      <form
        className="mt-4 flex flex-col gap-2 sm:flex-row"
        onSubmit={(event) => {
          event.preventDefault();
          run();
        }}
      >
        <TextInput
          type="url"
          inputMode="url"
          value={url}
          onChange={(event) => setUrl(event.target.value)}
          placeholder="https://example.com"
          aria-label="Website address"
          className="sm:flex-1"
          autoComplete="off"
        />
        <Button type="submit" variant="primary" loading={busy} disabled={!url.trim()}>
          Find feeds
        </Button>
      </form>

      {error ? (
        <div className="mt-3">
          <ErrorState error={error} compact />
        </div>
      ) : null}

      {feeds ? (
        feeds.length === 0 ? (
          <div className="mt-3">
            <Notice tone="warn" title="No feeds found on that page">
              Some sites publish no feed at all. Try the kind called HTML page in the form below,
              which reads the page itself.
            </Notice>
          </div>
        ) : (
          <ul className="mt-4 flex flex-col gap-2" aria-label="Discovered feeds">
            {feeds.map((feed) => {
              const state = added[feed.url];
              return (
                <li
                  key={feed.url}
                  className="flex flex-wrap items-center gap-2 rounded-lg border border-line p-3"
                >
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium break-anywhere">
                      {feed.title || feed.url}
                    </p>
                    <p className="text-xs text-muted break-anywhere">{feed.url}</p>
                  </div>
                  <Badge tone="neutral">{feed.kind}</Badge>
                  <Button
                    size="sm"
                    variant={state === "done" ? "ghost" : "secondary"}
                    disabled={state === "done"}
                    loading={state === "adding"}
                    onClick={() => add(feed)}
                  >
                    {state === "done" ? "Added" : state === "failed" ? "Retry" : "Add"}
                  </Button>
                </li>
              );
            })}
          </ul>
        )
      ) : null}
    </Card>
  );
}

/* -------------------------------------------------------------------------- */
/* Add by hand                                                                 */
/* -------------------------------------------------------------------------- */

interface ConfigRow {
  key: string;
  value: string;
}

function AddSourceForm({ slug, onAdded }: { slug: string; onAdded: () => void }) {
  const { config, loading: configLoading } = useConfig();
  const kinds = useMemo<SourceKind[]>(() => config?.source_kinds ?? [], [config]);

  const [open, setOpen] = useState(false);
  const [kind, setKind] = useState("");
  const [url, setUrl] = useState("");
  const [name, setName] = useState("");
  const [rows, setRows] = useState<ConfigRow[]>([{ key: "query", value: "" }]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [done, setDone] = useState("");

  useEffect(() => {
    if (kind || kinds.length === 0) return;
    const first = kinds.find((entry) => entry.available) ?? kinds[0];
    setKind(first.kind);
  }, [kinds, kind]);

  const selected = kinds.find((entry) => entry.kind === kind) ?? null;
  const urlless = selected?.urlless ?? false;

  const submit = () => {
    if (busy || !selected) return;
    setBusy(true);
    setError(null);
    setDone("");

    const configObject: JsonObject = {};
    if (urlless) {
      for (const row of rows) {
        const key = row.key.trim();
        if (!key) continue;
        configObject[key] = row.value;
      }
    }

    api
      .addSource(slug, {
        kind: selected.kind,
        // A kind with no address still has to send the field, so it sends an
        // empty one and carries its settings in config instead.
        url: urlless ? "" : url.trim(),
        name: name.trim() || undefined,
        config: urlless ? configObject : undefined,
      })
      .then((source) => {
        setDone(source.name || source.url || selected.label);
        setUrl("");
        setName("");
        setRows([{ key: "query", value: "" }]);
        onAdded();
      })
      .catch((cause: unknown) => setError(toApiError(cause)))
      .finally(() => setBusy(false));
  };

  const canSubmit = Boolean(
    selected && (urlless ? rows.some((row) => row.key.trim() && row.value.trim()) : url.trim()),
  );

  return (
    <Card className="p-4 sm:p-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-base font-semibold tracking-tight">Add a source by hand</h2>
        <Button size="sm" variant="ghost" onClick={() => setOpen((value) => !value)} aria-expanded={open}>
          {open ? "Hide" : "Show"}
        </Button>
      </div>

      {open ? (
        <form
          className="mt-4 flex flex-col gap-4"
          onSubmit={(event) => {
            event.preventDefault();
            submit();
          }}
        >
          {configLoading && kinds.length === 0 ? (
            <p className="text-sm text-muted">Loading the kinds this deployment supports.</p>
          ) : kinds.length === 0 ? (
            <Notice tone="warn" title="No source kinds were reported">
              The backend did not list any source kinds, so nothing can be added by hand yet.
            </Notice>
          ) : (
            <>
              <Select
                label="Kind"
                value={kind}
                onChange={(event) => setKind(event.target.value)}
                hint={selected?.url_hint || undefined}
              >
                {kinds.map((entry) => (
                  <option key={entry.kind} value={entry.kind} disabled={!entry.available}>
                    {entry.label}
                    {entry.available ? "" : " (unavailable here)"}
                  </option>
                ))}
              </Select>

              {selected && !selected.available ? (
                <Notice tone="warn" title={`${selected.label} is not available here`}>
                  {selected.reason}
                </Notice>
              ) : null}

              {selected && selected.key_names.length > 0 ? (
                <p className="text-xs text-muted break-anywhere">
                  Needs a credential: {selected.key_names.join(", ")}
                </p>
              ) : null}

              {urlless ? (
                <fieldset className="flex flex-col gap-2">
                  <legend className="text-sm font-medium">Settings</legend>
                  <p className="text-xs text-muted">
                    {selected?.url_hint ||
                      "This kind takes settings rather than an address. Name each one and give it a value."}
                  </p>
                  {rows.map((row, index) => (
                    <div key={index} className="flex flex-wrap gap-2">
                      <TextInput
                        value={row.key}
                        aria-label={`Setting ${index + 1} name`}
                        placeholder="query"
                        className="sm:max-w-40"
                        onChange={(event) =>
                          setRows((current) =>
                            current.map((entry, position) =>
                              position === index ? { ...entry, key: event.target.value } : entry,
                            ),
                          )
                        }
                      />
                      <TextInput
                        value={row.value}
                        aria-label={`Setting ${index + 1} value`}
                        placeholder="value"
                        className="flex-1"
                        onChange={(event) =>
                          setRows((current) =>
                            current.map((entry, position) =>
                              position === index ? { ...entry, value: event.target.value } : entry,
                            ),
                          )
                        }
                      />
                      {rows.length > 1 ? (
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() =>
                            setRows((current) => current.filter((_, position) => position !== index))
                          }
                        >
                          Remove
                        </Button>
                      ) : null}
                    </div>
                  ))}
                  <div>
                    <Button
                      size="sm"
                      variant="secondary"
                      onClick={() => setRows((current) => [...current, { key: "", value: "" }])}
                    >
                      Add another setting
                    </Button>
                  </div>
                </fieldset>
              ) : (
                <TextInput
                  label="Address"
                  type="url"
                  inputMode="url"
                  value={url}
                  onChange={(event) => setUrl(event.target.value)}
                  placeholder={selected?.url_hint || "https://example.com/feed.xml"}
                  hint={selected?.url_hint || undefined}
                  autoComplete="off"
                />
              )}

              <TextInput
                label="Name"
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="Optional label for this source"
                autoComplete="off"
              />

              {error ? <ErrorState error={error} compact /> : null}
              {done ? <Notice tone="ok" title={`Added ${done}`} /> : null}

              <div>
                <Button type="submit" variant="primary" loading={busy} disabled={!canSubmit}>
                  Add source
                </Button>
              </div>
            </>
          )}
        </form>
      ) : null}
    </Card>
  );
}

/* -------------------------------------------------------------------------- */
/* OPML                                                                        */
/* -------------------------------------------------------------------------- */

/** Past this many feeds, adding them all switched on would change what a run costs. */
const LARGE_IMPORT = 40;

/**
 * Feeds in and out in the format every reader speaks.
 *
 * This is what keeps the app from being a trap: a subscription list built up
 * over years arrives in one file, and leaves in one file.
 */
function OpmlCard({ slug, onImported }: { slug: string; onImported: () => void }) {
  const { config } = useConfig();
  const fileInput = useRef<HTMLInputElement>(null);

  const [filename, setFilename] = useState("");
  const [opml, setOpml] = useState("");
  const [enabled, setEnabled] = useState(true);
  const [importing, setImporting] = useState(false);
  const [importError, setImportError] = useState<ApiError | null>(null);
  const [result, setResult] = useState<OpmlImportResponse | null>(null);

  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<ApiError | null>(null);
  const [skipped, setSkipped] = useState<string[] | null>(null);

  // Counted in the browser so the warning arrives before the import, not after.
  // An xmlUrl is what makes an outline a feed, so counting them counts feeds.
  const feedsInFile = useMemo(() => (opml.match(/xmlUrl\s*=/gi) ?? []).length, [opml]);

  const kindLabel = useCallback(
    (kind: string) => config?.source_kinds.find((entry) => entry.kind === kind)?.label ?? kind,
    [config],
  );

  const download = () => {
    if (exporting) return;
    setExporting(true);
    setExportError(null);
    setSkipped(null);
    api
      .sourcesOpml(slug)
      .then((file) => {
        saveBlob(file.blob, file.filename);
        setSkipped(file.skippedKinds);
      })
      .catch((cause: unknown) => setExportError(toApiError(cause)))
      .finally(() => setExporting(false));
  };

  const choose = (file: File | null) => {
    setImportError(null);
    setResult(null);
    if (!file) {
      setFilename("");
      setOpml("");
      return;
    }
    setFilename(file.name);
    file
      .text()
      .then(setOpml)
      .catch(() => {
        setOpml("");
        setImportError(
          new ApiError(`${file.name} could not be read.`, {
            hint: "Pick the file again, or export a fresh one from your reader.",
          }),
        );
      });
  };

  const upload = () => {
    if (importing || !opml.trim()) return;
    setImporting(true);
    setImportError(null);
    setResult(null);
    api
      .importOpml(slug, { opml, enabled })
      .then((response) => {
        setResult(response);
        onImported();
      })
      .catch((cause: unknown) => setImportError(toApiError(cause)))
      .finally(() => setImporting(false));
  };

  const clear = () => {
    setFilename("");
    setOpml("");
    setResult(null);
    setImportError(null);
    if (fileInput.current) fileInput.current.value = "";
  };

  return (
    <Card className="p-4 sm:p-5">
      <h2 className="text-base font-semibold tracking-tight">Move a subscription list</h2>
      <p className="mt-1 text-sm text-muted">
        OPML is the file every feed reader imports and exports. Bring years of subscriptions in
        with one, or take this workspace&apos;s sources out with one.
      </p>

      <div className="mt-4 flex flex-col gap-4 sm:flex-row sm:items-start sm:gap-6">
        <div className="sm:flex-1">
          <h3 className="text-sm font-semibold">Take them out</h3>
          <p className="mt-1 text-xs text-muted">
            Every feed shaped source in this workspace, as one file.
          </p>
          <div className="mt-2">
            <Button variant="secondary" size="sm" onClick={download} loading={exporting}>
              Download OPML
            </Button>
          </div>
        </div>

        <div className="sm:flex-1">
          <h3 className="text-sm font-semibold">Bring them in</h3>
          <p className="mt-1 text-xs text-muted">
            Choose an .opml or .xml file exported from another reader.
          </p>
          <input
            ref={fileInput}
            type="file"
            accept=".opml,.xml,text/xml,application/xml,text/x-opml"
            aria-label="OPML file to import"
            onChange={(event) => choose(event.target.files?.[0] ?? null)}
            className="mt-2 block w-full text-sm text-muted file:mr-3 file:rounded-lg file:border file:border-line file:bg-surface file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-ink hover:file:bg-raised"
          />
        </div>
      </div>

      {exportError ? (
        <div className="mt-3">
          <ErrorState error={exportError} compact />
        </div>
      ) : null}

      {skipped ? (
        <div className="mt-3">
          {skipped.length > 0 ? (
            <Notice tone="warn" title="Some sources are not in that file">
              {skipped.map(kindLabel).join(", ")} could not be written. OPML describes a feed by
              its address and nothing else, and these kinds have no address to give: a Google
              News query or a subreddit is a search this app runs, not a feed another reader
              could subscribe to. They are still here, they just do not travel.
            </Notice>
          ) : (
            <Notice tone="ok" title="Saved">
              Every source in this workspace is in that file.
            </Notice>
          )}
        </div>
      ) : null}

      {opml ? (
        <div className="mt-4 rounded-lg border border-line p-3">
          <p className="text-sm font-medium break-anywhere">{filename}</p>
          <p className="mt-0.5 text-xs text-muted">
            {feedsInFile > 0
              ? `${plural(feedsInFile, "feed")} in the file.`
              : "No feeds found in that file. The backend will say for certain."}
          </p>

          <div className="mt-3">
            <Toggle
              checked={enabled}
              onChange={setEnabled}
              label="Start these sources switched on"
              description={
                "Off, they are added disabled and collect nothing until you turn each one on. " +
                "That is the safer way to bring in a long list, because every enabled source is " +
                "fetched on every run."
              }
            />
          </div>

          {feedsInFile >= LARGE_IMPORT && enabled ? (
            <div className="mt-3">
              <Notice tone="warn" title="That is a lot of feeds to switch on at once">
                {plural(feedsInFile, "feed")} fetched on every run will make runs slower and, if
                a model is doing the summarising, dearer. Consider adding them switched off and
                turning on the ones you actually read.
              </Notice>
            </div>
          ) : null}

          <p className="mt-3 text-xs text-muted">
            Importing the same file twice is safe. A feed already here is counted as already
            present rather than added again, so nothing is duplicated.
          </p>

          {importError ? (
            <div className="mt-3">
              <ErrorState error={importError} compact />
            </div>
          ) : null}

          {result ? (
            <div className="mt-3">
              <Notice
                tone={result.added > 0 ? "ok" : "neutral"}
                title={
                  result.added > 0
                    ? `Added ${plural(result.added, "source")}`
                    : "Nothing new to add"
                }
              >
                <ul className="flex flex-col gap-0.5">
                  <li>{plural(result.total_in_file, "feed")} in the file.</li>
                  <li>{plural(result.added, "source")} added to this workspace.</li>
                  <li>
                    {plural(result.already_present, "feed")} already here, so nothing was
                    duplicated.
                  </li>
                  <li>
                    {plural(result.skipped, "entry", "entries")} skipped, having no feed address
                    to subscribe to.
                  </li>
                </ul>
              </Notice>
            </div>
          ) : null}

          <div className="mt-3 flex flex-wrap gap-2">
            <Button variant="primary" onClick={upload} loading={importing} disabled={!opml.trim()}>
              {result ? "Import again" : "Import these feeds"}
            </Button>
            <Button variant="ghost" onClick={clear}>
              Clear
            </Button>
          </div>
        </div>
      ) : null}
    </Card>
  );
}

/* -------------------------------------------------------------------------- */
/* One source                                                                  */
/* -------------------------------------------------------------------------- */

function healthTone(source: Source): BadgeTone {
  if (!source.enabled) return "neutral";
  if (source.consecutive_failures >= 3) return "danger";
  if (source.consecutive_failures > 0) return "warn";
  if (source.last_status && source.last_status.toLowerCase() !== "ok") return "warn";
  if (source.last_success_at) return "ok";
  return "neutral";
}

function healthLabel(source: Source): string {
  if (!source.enabled) return "Disabled";
  if (source.consecutive_failures >= 3) return `Failing (${source.consecutive_failures})`;
  if (source.consecutive_failures > 0) return `Shaky (${source.consecutive_failures})`;
  if (source.last_status) return source.last_status;
  return "Not run yet";
}

function SourceRow({
  source,
  onChanged,
  onRemoved,
}: {
  source: Source;
  onChanged: (next: Source) => void;
  onRemoved: (id: Id) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  const toggle = (enabled: boolean) => {
    setBusy(true);
    setError(null);
    api
      .updateSource(source.id, { enabled })
      .then(onChanged)
      .catch((cause: unknown) => setError(toApiError(cause)))
      .finally(() => setBusy(false));
  };

  const remove = () => {
    setBusy(true);
    setError(null);
    api
      .deleteSource(source.id)
      .then(() => onRemoved(source.id))
      .catch((cause: unknown) => {
        setError(toApiError(cause));
        setBusy(false);
      });
  };

  return (
    <li
      className={cx(
        "rounded-xl border border-line bg-surface p-3 sm:p-4",
        !source.enabled && "opacity-70",
      )}
    >
      <div className="flex flex-wrap items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="min-w-0 text-sm font-semibold break-anywhere">
              {source.name || source.url || source.kind}
            </h3>
            <Badge tone="neutral">{source.kind}</Badge>
            <Badge tone={healthTone(source)}>{healthLabel(source)}</Badge>
          </div>
          {source.url ? (
            <a
              href={source.url}
              target="_blank"
              rel="noopener noreferrer"
              className="mt-1 inline-block rounded-sm text-xs text-muted underline underline-offset-2 break-anywhere"
            >
              {source.url}
            </a>
          ) : null}
          <dl className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
            <div className="flex gap-1">
              <dt>Items seen</dt>
              <dd className="font-medium text-ink">{source.items_seen}</dd>
            </div>
            <div className="flex gap-1">
              <dt>Last run</dt>
              <dd className="font-medium text-ink">{relativeTime(source.last_run_at)}</dd>
            </div>
            <div className="flex gap-1">
              <dt>Last success</dt>
              <dd className="font-medium text-ink">{relativeTime(source.last_success_at)}</dd>
            </div>
            <div className="flex gap-1">
              <dt>Failures in a row</dt>
              <dd className="font-medium text-ink">{source.consecutive_failures}</dd>
            </div>
          </dl>
          {source.last_error ? (
            <p className="mt-2 rounded-md bg-danger-soft px-2 py-1.5 text-xs text-danger break-anywhere">
              {source.last_error}
            </p>
          ) : null}
        </div>

        <div className="flex shrink-0 flex-col items-end gap-2">
          <Toggle
            checked={source.enabled}
            disabled={busy}
            onChange={toggle}
            label={source.enabled ? "Enabled" : "Disabled"}
          />
          {confirming ? (
            <div className="flex items-center gap-1.5">
              <Button size="sm" variant="danger" onClick={remove} loading={busy}>
                Delete
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setConfirming(false)}>
                Keep
              </Button>
            </div>
          ) : (
            <Button size="sm" variant="ghost" onClick={() => setConfirming(true)} disabled={busy}>
              Remove
            </Button>
          )}
        </div>
      </div>

      {error ? (
        <div className="mt-3">
          <ErrorState error={error} compact />
        </div>
      ) : null}
    </li>
  );
}
