"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import { ErrorState, SkeletonCards, SkeletonRows } from "@/components/states";
import {
  Badge,
  Button,
  Card,
  Notice,
  SectionHeading,
  TextArea,
  TextInput,
  Toggle,
  cx,
} from "@/components/ui";
import { ApiError, api, toApiError } from "@/lib/api";
import { absoluteDate, absoluteDateTime, slugify } from "@/lib/format";
import { useAsync } from "@/lib/hooks";
import type { DeliveryResult, Digest, Id, Theme } from "@/lib/types";

/**
 * A new theme needs an id the backend would have chosen itself. Existing
 * themes never pass through here, so an id already in use is never rewritten.
 */
function themeId(label: string): string {
  return slugify(label) || label.trim().toLowerCase();
}

/**
 * The contract does not expose which delivery channels a deployment has wired
 * up, so a common set is offered and anything else can be typed in. A channel
 * the backend does not know about comes back as a failed result rather than a
 * silent no op.
 */
const SUGGESTED_CHANNELS = ["email", "slack", "discord", "webhook"];

export function WorkspaceSettingsView({ slug }: { slug: string }) {
  const load = useCallback((signal: AbortSignal) => api.getWorkspace(slug, signal), [slug]);
  const { data, error, initialising, reload } = useAsync(load, [slug]);

  return (
    <div className="flex flex-col gap-6">
      {initialising ? (
        <SkeletonCards count={2} lines={4} />
      ) : error ? (
        <ErrorState error={error} onRetry={reload} title="Could not load this workspace" />
      ) : data ? (
        <>
          <WorkspaceDetailsCard
            slug={slug}
            initialName={data.name}
            initialDescription={data.description}
            initialTimezone={data.timezone}
            initialThemes={data.themes}
            initialEnabled={data.enabled}
            onSaved={reload}
          />
          <DigestCard slug={slug} />
          <DangerZone slug={slug} name={data.name} />
        </>
      ) : null}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Details                                                                     */
/* -------------------------------------------------------------------------- */

function WorkspaceDetailsCard({
  slug,
  initialName,
  initialDescription,
  initialTimezone,
  initialThemes,
  initialEnabled,
  onSaved,
}: {
  slug: string;
  initialName: string;
  initialDescription: string;
  initialTimezone: string;
  initialThemes: Theme[];
  initialEnabled: boolean;
  onSaved: () => void;
}) {
  const [name, setName] = useState(initialName);
  const [description, setDescription] = useState(initialDescription ?? "");
  const [timezone, setTimezone] = useState(initialTimezone ?? "UTC");
  const [themes, setThemes] = useState<Theme[]>(initialThemes ?? []);
  const [newTheme, setNewTheme] = useState("");
  const [enabled, setEnabled] = useState(initialEnabled);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [saved, setSaved] = useState(false);

  const addTheme = () => {
    const label = newTheme.trim();
    if (!label) return;
    const id = themeId(label);
    setThemes((current) =>
      current.some((entry) => entry.id === id || entry.label === label)
        ? current
        : [...current, { id, label, hint: "" }],
    );
    setNewTheme("");
    setSaved(false);
  };

  // Only the label and the hint are editable. The id stays exactly as it came
  // back, because every article and event already filed carries it.
  const editTheme = (id: string, patch: Partial<Pick<Theme, "label" | "hint">>) => {
    setThemes((current) =>
      current.map((entry) => (entry.id === id ? { ...entry, ...patch } : entry)),
    );
    setSaved(false);
  };

  const save = () => {
    if (busy) return;
    setBusy(true);
    setError(null);
    setSaved(false);
    api
      .updateWorkspace(slug, {
        name: name.trim(),
        description: description.trim(),
        timezone: timezone.trim(),
        themes,
        enabled,
      })
      .then(() => {
        setSaved(true);
        onSaved();
      })
      .catch((cause: unknown) => setError(toApiError(cause)))
      .finally(() => setBusy(false));
  };

  return (
    <Card className="p-4 sm:p-6">
      <SectionHeading
        title="Workspace"
        description="What this beat is called, when its day starts, and how stories are filed."
      />

      <div className="grid gap-4 sm:grid-cols-2">
        <TextInput
          label="Name"
          value={name}
          onChange={(event) => {
            setName(event.target.value);
            setSaved(false);
          }}
        />
        <TextInput
          label="Time zone"
          value={timezone}
          onChange={(event) => {
            setTimezone(event.target.value);
            setSaved(false);
          }}
          hint="An IANA name such as Europe/London. Decides when a digest day ends."
        />
        <TextArea
          label="Description"
          className="sm:col-span-2"
          rows={2}
          value={description}
          onChange={(event) => {
            setDescription(event.target.value);
            setSaved(false);
          }}
        />
      </div>

      <fieldset className="mt-5">
        <legend className="text-sm font-medium">Themes</legend>
        <p className="mt-1 text-xs text-muted">
          Every story is filed under one of these. The hint is the sentence the classifier is
          given to decide what belongs here, so it is worth being specific. Removing a theme
          does not delete anything already filed under it.
        </p>

        <ul className="mt-3 flex flex-col gap-2">
          {themes.map((theme) => (
            <li key={theme.id} className="rounded-lg border border-line p-3">
              <div className="flex flex-wrap items-end gap-2">
                <TextInput
                  label="Label"
                  value={theme.label}
                  onChange={(event) => editTheme(theme.id, { label: event.target.value })}
                  className="sm:max-w-64"
                />
                <Button
                  variant="ghost"
                  size="sm"
                  className="mb-1 ml-auto"
                  onClick={() => {
                    setThemes((current) => current.filter((entry) => entry.id !== theme.id));
                    setSaved(false);
                  }}
                >
                  Remove
                </Button>
              </div>
              <TextArea
                label="Hint for the classifier"
                className="mt-2"
                rows={2}
                value={theme.hint}
                onChange={(event) => editTheme(theme.id, { hint: event.target.value })}
                placeholder="What belongs under this theme, and what does not."
              />
              <p className="mt-1 font-mono text-xs text-faint break-anywhere">id: {theme.id}</p>
            </li>
          ))}
          {themes.length === 0 ? (
            <li className="text-sm text-muted">
              No themes yet. Everything will land in one pile.
            </li>
          ) : null}
        </ul>

        <div className="mt-3 flex flex-wrap gap-2">
          <TextInput
            value={newTheme}
            onChange={(event) => setNewTheme(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                addTheme();
              }
            }}
            placeholder="Add a theme"
            aria-label="New theme label"
            className="sm:max-w-64"
          />
          <Button variant="secondary" onClick={addTheme} disabled={!newTheme.trim()}>
            Add
          </Button>
        </div>
      </fieldset>

      <div className="mt-5">
        <Toggle
          checked={enabled}
          onChange={(value) => {
            setEnabled(value);
            setSaved(false);
          }}
          label="Collecting is on"
          description="Turn this off to leave the archive in place but stop new runs."
        />
      </div>

      {error ? (
        <div className="mt-4">
          <ErrorState error={error} compact />
        </div>
      ) : null}

      <div className="mt-5 flex flex-wrap items-center gap-3">
        <Button variant="primary" onClick={save} loading={busy}>
          Save changes
        </Button>
        {saved ? <span className="text-sm text-ok">Saved.</span> : null}
      </div>
    </Card>
  );
}

/* -------------------------------------------------------------------------- */
/* Digests                                                                     */
/* -------------------------------------------------------------------------- */

function DigestCard({ slug }: { slug: string }) {
  const load = useCallback((signal: AbortSignal) => api.listDigests(slug, signal), [slug]);
  const { data, error, initialising, reload } = useAsync(load, [slug]);

  const [generating, setGenerating] = useState(false);
  const [generateError, setGenerateError] = useState<ApiError | null>(null);
  const [selectedId, setSelectedId] = useState<Id | null>(null);

  const digests = useMemo(() => data ?? [], [data]);

  useEffect(() => {
    if (selectedId === null && digests.length > 0) {
      setSelectedId(digests[0].id);
    }
  }, [digests, selectedId]);

  const generate = () => {
    if (generating) return;
    setGenerating(true);
    setGenerateError(null);
    api
      .generateDigest(slug)
      .then((digest) => {
        setSelectedId(digest.id);
        reload();
      })
      .catch((cause: unknown) => setGenerateError(toApiError(cause)))
      .finally(() => setGenerating(false));
  };

  return (
    <Card className="p-4 sm:p-6">
      <SectionHeading
        title="Digests"
        description="A written summary of a period, which can be sent onwards."
        action={
          <Button variant="primary" onClick={generate} loading={generating}>
            Generate now
          </Button>
        }
      />

      {generateError ? (
        <div className="mb-4">
          <ErrorState error={generateError} compact />
        </div>
      ) : null}

      {initialising ? (
        <SkeletonRows count={3} />
      ) : error ? (
        <ErrorState error={error} onRetry={reload} compact title="Could not list digests" />
      ) : digests.length === 0 ? (
        <Notice tone="neutral" title="No digests yet">
          Generate one to see what a period reads like, then deliver it to a channel.
        </Notice>
      ) : (
        <div className="grid gap-4 lg:grid-cols-[minmax(0,18rem)_minmax(0,1fr)]">
          <ul className="flex max-h-80 flex-col gap-1.5 overflow-y-auto pr-1">
            {digests.map((digest) => (
              <li key={String(digest.id)}>
                <button
                  type="button"
                  onClick={() => setSelectedId(digest.id)}
                  aria-current={digest.id === selectedId ? "true" : undefined}
                  className={cx(
                    "w-full rounded-lg border p-3 text-left transition-colors",
                    digest.id === selectedId
                      ? "border-accent bg-accent-soft"
                      : "border-line hover:border-line-strong",
                  )}
                >
                  <span className="block text-sm font-medium break-anywhere">{digest.title}</span>
                  <span className="mt-0.5 block text-xs text-muted">
                    {digest.period_start ? absoluteDate(digest.period_start) : ""}
                    {digest.period_start && digest.period_end ? " to " : ""}
                    {digest.period_end ? absoluteDate(digest.period_end) : ""}
                  </span>
                </button>
              </li>
            ))}
          </ul>

          {selectedId !== null ? <DigestDetail id={selectedId} /> : null}
        </div>
      )}
    </Card>
  );
}

function DigestDetail({ id }: { id: Id }) {
  const load = useCallback((signal: AbortSignal) => api.getDigest(id, signal), [id]);
  const { data, error, initialising, reload } = useAsync<Digest>(load, [id]);

  if (initialising) return <SkeletonCards count={1} lines={5} />;
  if (error) return <ErrorState error={error} onRetry={reload} compact title="Could not open that digest" />;
  if (!data) return null;

  return (
    <div className="min-w-0 rounded-lg border border-line p-4">
      <h3 className="text-base font-semibold break-anywhere">{data.title}</h3>
      <p className="mt-1 text-xs text-muted">
        {data.created_at ? `Created ${absoluteDateTime(data.created_at)}` : null}
      </p>

      <div className="mt-4 max-h-96 overflow-y-auto">
        {data.html ? (
          // The digest HTML is produced by this app's own backend from its own
          // article store, so it is rendered rather than escaped.
          <div className="prose-digest text-sm" dangerouslySetInnerHTML={{ __html: data.html }} />
        ) : (
          <pre className="text-sm whitespace-pre-wrap break-anywhere">{data.markdown}</pre>
        )}
      </div>

      <DeliveryPanel id={data.id} />
    </div>
  );
}

function DeliveryPanel({ id }: { id: Id }) {
  const [channels, setChannels] = useState<string[]>([]);
  const [custom, setCustom] = useState("");
  const [to, setTo] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [results, setResults] = useState<DeliveryResult[] | null>(null);

  const toggleChannel = (channel: string) => {
    setResults(null);
    setChannels((current) =>
      current.includes(channel)
        ? current.filter((entry) => entry !== channel)
        : [...current, channel],
    );
  };

  const deliver = () => {
    const all = [...channels];
    const extra = custom.trim();
    if (extra && !all.includes(extra)) all.push(extra);
    if (all.length === 0 || busy) return;

    setBusy(true);
    setError(null);
    setResults(null);
    api
      .deliverDigest(id, { channels: all, to: to.trim() || undefined })
      .then((response) => setResults(response.results))
      .catch((cause: unknown) => setError(toApiError(cause)))
      .finally(() => setBusy(false));
  };

  const needsTo = channels.includes("email") || custom.trim() === "email";

  return (
    <div className="mt-5 border-t border-line pt-4">
      <h4 className="text-sm font-semibold">Deliver this digest</h4>
      <div className="mt-2 flex flex-wrap gap-2">
        {SUGGESTED_CHANNELS.map((channel) => (
          <button
            key={channel}
            type="button"
            onClick={() => toggleChannel(channel)}
            aria-pressed={channels.includes(channel)}
            className={cx(
              "rounded-full border px-3 py-1 text-sm font-medium transition-colors",
              channels.includes(channel)
                ? "border-transparent bg-accent text-accent-ink"
                : "border-line text-muted hover:text-ink",
            )}
          >
            {channel}
          </button>
        ))}
      </div>

      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        <TextInput
          label="Another channel"
          value={custom}
          onChange={(event) => setCustom(event.target.value)}
          placeholder="Any channel this deployment configured"
          autoComplete="off"
        />
        <TextInput
          label="Send to"
          value={to}
          onChange={(event) => setTo(event.target.value)}
          placeholder={needsTo ? "you@example.com" : "Optional address or target"}
          autoComplete="off"
        />
      </div>

      {error ? (
        <div className="mt-3">
          <ErrorState error={error} compact />
        </div>
      ) : null}

      {results ? (
        <ul className="mt-3 flex flex-col gap-1.5">
          {results.map((result, index) => (
            <li
              key={`${result.channel}-${index}`}
              className="flex flex-wrap items-center gap-2 text-sm"
            >
              <Badge tone={result.error ? "danger" : "ok"}>{result.channel}</Badge>
              <span className="text-muted">{result.status}</span>
              {result.error ? (
                <span className="text-danger break-anywhere">{result.error}</span>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}

      <div className="mt-3">
        <Button
          variant="secondary"
          onClick={deliver}
          loading={busy}
          disabled={channels.length === 0 && !custom.trim()}
        >
          Deliver
        </Button>
      </div>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Danger zone                                                                 */
/* -------------------------------------------------------------------------- */

function DangerZone({ slug, name }: { slug: string; name: string }) {
  const router = useRouter();
  const [confirmation, setConfirmation] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  const remove = () => {
    if (busy) return;
    setBusy(true);
    setError(null);
    api
      .deleteWorkspace(slug)
      .then(() => router.push("/"))
      .catch((cause: unknown) => {
        setError(toApiError(cause));
        setBusy(false);
      });
  };

  return (
    <Card className="border-danger-soft p-4 sm:p-6">
      <SectionHeading
        title="Delete this workspace"
        description="Its sources, articles, events and digests go with it. This cannot be undone."
      />
      <div className="flex flex-wrap items-end gap-3">
        <TextInput
          label="Type the slug to confirm"
          value={confirmation}
          onChange={(event) => setConfirmation(event.target.value)}
          placeholder={slug}
          hint={`This deletes "${name}".`}
          className="sm:max-w-64"
          autoComplete="off"
        />
        <Button
          variant="danger"
          onClick={remove}
          loading={busy}
          disabled={confirmation.trim() !== slug}
        >
          Delete permanently
        </Button>
      </div>
      {error ? (
        <div className="mt-4">
          <ErrorState error={error} compact />
        </div>
      ) : null}
    </Card>
  );
}
