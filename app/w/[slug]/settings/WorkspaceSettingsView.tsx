"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import { useConfig } from "@/components/ConfigProvider";
import { ErrorState, SkeletonCards, SkeletonRows } from "@/components/states";
import {
  Badge,
  Button,
  Card,
  Notice,
  SectionHeading,
  Select,
  TextArea,
  TextInput,
  Toggle,
  cx,
} from "@/components/ui";
import { ApiError, api, toApiError } from "@/lib/api";
import { linkButtonClass } from "@/lib/classes";
import { absoluteDate, absoluteDateTime, slugify } from "@/lib/format";
import { useAsync } from "@/lib/hooks";
import type {
  DeliveryChannel,
  DeliveryResult,
  Digest,
  DigestExportFormat,
  Id,
  Theme,
  WorkspaceConfig,
} from "@/lib/types";

/**
 * A new theme needs an id the backend would have chosen itself. Existing
 * themes never pass through here, so an id already in use is never rewritten.
 */
function themeId(label: string): string {
  return slugify(label) || label.trim().toLowerCase();
}

/**
 * Used only when /api/config did not answer. A channel the backend does not
 * know about comes back as a failed result rather than a silent no op, so a
 * guess here is safe.
 */
const SUGGESTED_CHANNELS = ["email", "slack", "discord", "webhook"];

/**
 * Whether this workspace has an opinion about automatic delivery.
 *
 * The backend reads `auto_deliver` with the deployment's own AUTO_DELIVER as
 * the fallback, so an absent key is a third answer and not the same as false.
 * A plain switch would have to claim to know AUTO_DELIVER, which nothing here
 * can see, so the choice is offered as it actually is.
 */
type AutoDeliverChoice = "inherit" | "on" | "off";

function autoDeliverChoice(config: WorkspaceConfig): AutoDeliverChoice {
  if (config.auto_deliver === true) return "on";
  if (config.auto_deliver === false) return "off";
  return "inherit";
}

function storedChannels(config: WorkspaceConfig): string[] {
  const stored = config.delivery_channels;
  if (!Array.isArray(stored)) return [];
  return stored.filter((entry): entry is string => typeof entry === "string");
}

/**
 * The channels to offer, and whether their configured flags are real.
 *
 * Until /api/config answers there is no way to tell a wired up channel from a
 * missing one. Printing "(not configured)" against all of them meanwhile would
 * be a claim the page cannot support, and would libel the one channel that is
 * always configured, so the fallback list is offered unlabelled instead.
 */
function useDeliveryChannels(): {
  channels: DeliveryChannel[];
  known: boolean;
  loading: boolean;
} {
  const { config, loading } = useConfig();
  const known = config?.delivery_channels ?? null;
  const channels = useMemo<DeliveryChannel[]>(
    () =>
      known ??
      SUGGESTED_CHANNELS.map((channelId) => ({
        id: channelId,
        label: channelId,
        configured: false,
        needs_target: channelId === "email" || channelId === "webhook",
      })),
    [known],
  );
  return { channels, known: known !== null, loading };
}

/** Said once, wherever a channel list is shown before the answer has arrived. */
function ChannelsUnknownNote({ loading }: { loading: boolean }) {
  return (
    <p className="mt-2 text-xs text-muted">
      {loading
        ? "Checking which of these the deployment has credentials for."
        : "Which of these the deployment has credentials for could not be read, so none is marked either way."}
    </p>
  );
}

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
            initialConfig={data.config ?? {}}
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
  initialConfig,
  onSaved,
}: {
  slug: string;
  initialName: string;
  initialDescription: string;
  initialTimezone: string;
  initialThemes: Theme[];
  initialEnabled: boolean;
  initialConfig: WorkspaceConfig;
  onSaved: () => void;
}) {
  const [name, setName] = useState(initialName);
  const [description, setDescription] = useState(initialDescription ?? "");
  const [timezone, setTimezone] = useState(initialTimezone ?? "UTC");
  const [themes, setThemes] = useState<Theme[]>(initialThemes ?? []);
  const [newTheme, setNewTheme] = useState("");
  const [enabled, setEnabled] = useState(initialEnabled);
  // Absent means on, which is what the backend assumes when the key is missing.
  const [aiEnabled, setAiEnabled] = useState(initialConfig.ai_enabled !== false);
  const [applyKeywords, setApplyKeywords] = useState(initialConfig.apply_keywords !== false);
  const [applySourceWeight, setApplySourceWeight] = useState(
    initialConfig.apply_source_weight !== false,
  );
  const [autoDeliver, setAutoDeliver] = useState<AutoDeliverChoice>(() =>
    autoDeliverChoice(initialConfig),
  );
  const [deliveryChannels, setDeliveryChannels] = useState<string[]>(() =>
    storedChannels(initialConfig),
  );
  const [deliveryTo, setDeliveryTo] = useState(
    typeof initialConfig.delivery_to === "string" ? initialConfig.delivery_to : "",
  );
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

    // A PATCH replaces the stored config outright, so every key that came back
    // is sent again, including any this screen does not render.
    const config: WorkspaceConfig = {
      ...initialConfig,
      ai_enabled: aiEnabled,
      apply_keywords: applyKeywords,
      apply_source_weight: applySourceWeight,
      delivery_channels: deliveryChannels,
      delivery_to: deliveryTo.trim(),
    };
    // Leaving it out is the third answer: the deployment's AUTO_DELIVER decides.
    if (autoDeliver === "inherit") delete config.auto_deliver;
    else config.auto_deliver = autoDeliver === "on";

    api
      .updateWorkspace(slug, {
        name: name.trim(),
        description: description.trim(),
        timezone: timezone.trim(),
        themes,
        enabled,
        config,
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

      <div className="mt-5 flex flex-col gap-4">
        <Toggle
          checked={enabled}
          onChange={(value) => {
            setEnabled(value);
            setSaved(false);
          }}
          label="Collecting is on"
          description="Turn this off to leave the archive in place but stop new runs."
        />
        <Toggle
          checked={aiEnabled}
          onChange={(value) => {
            setAiEnabled(value);
            setSaved(false);
          }}
          label="Let runs call an AI model"
          description={
            "Off, a run makes no calls to any model at all: no theme classification, no event " +
            "summaries, no digest bullets and no enrichment. It still deduplicates and clusters, " +
            "because that runs on a bundled local model that costs nothing and needs no key. " +
            "Measured on this deployment: about 11 seconds for a run with this off, against " +
            "about 292 seconds with it on."
          }
        />
        <Toggle
          checked={applyKeywords}
          onChange={(value) => {
            setApplyKeywords(value);
            setSaved(false);
          }}
          label="Apply this workspace's keywords"
          description="Keywords act at ingest. Excluded words drop a story before anything is spent on it, and boosted words lift one up the digest. An include list, if this workspace has one, is a whitelist: everything it does not match is dropped, so leave it empty unless that is what you want."
        />
        <Toggle
          checked={applySourceWeight}
          onChange={(value) => {
            setApplySourceWeight(value);
            setSaved(false);
          }}
          label="Apply source weight"
          description="Source weight nudges ranking: a source you trust more lifts its stories up the digest without hiding anyone else's."
        />
      </div>

      <AutoDeliverySection
        choice={autoDeliver}
        onChoice={(value) => {
          setAutoDeliver(value);
          setSaved(false);
        }}
        channels={deliveryChannels}
        onChannels={(value) => {
          setDeliveryChannels(value);
          setSaved(false);
        }}
        to={deliveryTo}
        onTo={(value) => {
          setDeliveryTo(value);
          setSaved(false);
        }}
      />

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
/* Automatic delivery                                                          */
/* -------------------------------------------------------------------------- */

/**
 * Sending the digest without being asked.
 *
 * Two things about this are easy to get wrong and are therefore said on the
 * page: it happens only after a scheduled run, and a channel works only if the
 * deployment holds its credential. Neither is something this screen can fix.
 */
function AutoDeliverySection({
  choice,
  onChoice,
  channels,
  onChannels,
  to,
  onTo,
}: {
  choice: AutoDeliverChoice;
  onChoice: (next: AutoDeliverChoice) => void;
  channels: string[];
  onChannels: (next: string[]) => void;
  to: string;
  onTo: (next: string) => void;
}) {
  const { channels: available, known, loading } = useDeliveryChannels();

  const toggle = (id: string) => {
    onChannels(channels.includes(id) ? channels.filter((entry) => entry !== id) : [...channels, id]);
  };

  const chosen = available.filter((channel) => channels.includes(channel.id));
  const needsTarget = chosen.some((channel) => channel.needs_target);
  const unconfigured = known ? chosen.filter((channel) => !channel.configured) : [];

  return (
    <fieldset className="mt-6 rounded-lg border border-line p-3 sm:p-4">
      <legend className="px-1 text-sm font-medium">Send the digest automatically</legend>
      <p className="mt-1 text-xs text-muted">
        This fires at the end of a <strong>scheduled</strong> run only. Pressing Run now never
        sends anything, however this is set, so a manual run stays a manual run.
      </p>

      <div className="mt-3 flex flex-col gap-3">
        <Select
          label="Automatic delivery"
          value={choice}
          onChange={(event) => onChoice(event.target.value as AutoDeliverChoice)}
          hint={
            choice === "inherit"
              ? "Whatever the deployment's AUTO_DELIVER says, which is off unless it was set. This page cannot read that value, so it defers rather than guessing."
              : choice === "on"
                ? "On for this workspace, whatever the deployment says."
                : "Off for this workspace, even if the deployment turns it on everywhere else."
          }
          className="sm:max-w-80"
        >
          <option value="inherit">Follow the deployment</option>
          <option value="on">On for this workspace</option>
          <option value="off">Off for this workspace</option>
        </Select>

        <div>
          <p className="text-sm font-medium">Channels</p>
          <p className="mt-0.5 text-xs text-muted">
            Left empty, a scheduled send falls back to the deployment&apos;s own channel list.
          </p>
          <div className="mt-2 flex flex-wrap gap-2">
            {available.map((channel) => {
              const active = channels.includes(channel.id);
              return (
                <button
                  key={channel.id}
                  type="button"
                  onClick={() => toggle(channel.id)}
                  aria-pressed={active}
                  title={
                    !known
                      ? `Whether ${channel.label} is configured here is not known yet.`
                      : channel.configured
                        ? channel.needs_target
                          ? `${channel.label} needs an address to send to.`
                          : `${channel.label} is ready.`
                        : `${channel.label} has no credential on this deployment, so a send to it would fail.`
                  }
                  className={cx(
                    "rounded-full border px-3 py-1 text-sm font-medium transition-colors",
                    active
                      ? "border-transparent bg-accent text-accent-ink"
                      : "border-line text-muted hover:text-ink",
                    known && !channel.configured && !active && "opacity-55",
                  )}
                >
                  {channel.label}
                  {known && !channel.configured ? " (not configured)" : ""}
                </button>
              );
            })}
          </div>
          {known ? null : <ChannelsUnknownNote loading={loading} />}
        </div>

        <TextInput
          label="Send to"
          value={to}
          onChange={(event) => onTo(event.target.value)}
          placeholder={needsTarget ? "you@example.com" : "Optional address or target"}
          hint={
            needsTarget
              ? "Required: at least one channel you picked sends to an address rather than a fixed hook."
              : "Only channels that ask for a target use this. Slack and Discord post to the hook the deployment configured."
          }
          className="sm:max-w-96"
          autoComplete="off"
        />

        {unconfigured.length > 0 ? (
          <Notice tone="warn" title="Some of those channels are not configured here">
            {unconfigured.map((channel) => channel.label).join(", ")} would fail at send time.
            A channel is wired up on the server, by setting its credential there, not on this
            page.
          </Notice>
        ) : null}

        {needsTarget && !to.trim() ? (
          <Notice tone="warn" title="No address to send to">
            {chosen
              .filter((channel) => channel.needs_target)
              .map((channel) => channel.label)
              .join(", ")}{" "}
            needs one. Without it the run falls back to the deployment&apos;s own address, and
            fails if there is not one.
          </Notice>
        ) : null}
      </div>
    </fieldset>
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

      <ExportPanel id={data.id} />
      <DeliveryPanel id={data.id} />
    </div>
  );
}

const EXPORTS: Array<{ format: DigestExportFormat; label: string; hint: string }> = [
  { format: "md", label: "Markdown", hint: "The digest as it was written, for a notebook or a wiki" },
  { format: "csv", label: "CSV", hint: "One row per story, for a spreadsheet" },
  { format: "json", label: "JSON", hint: "The whole payload, for anything that reads it back" },
  { format: "pdf", label: "PDF", hint: "Laid out to read or print" },
];

/**
 * Saving a digest as a file.
 *
 * These are plain links, not fetches. The backend answers with a
 * Content-Disposition, so the browser streams it straight to disk: nothing has
 * to hold a PDF in memory to hand it back to the same browser that asked.
 */
function ExportPanel({ id }: { id: Id }) {
  return (
    <div className="mt-5 border-t border-line pt-4">
      <h4 className="text-sm font-semibold">Save this digest</h4>
      <div className="mt-2 flex flex-wrap gap-2">
        {EXPORTS.map((entry) => (
          <a
            key={entry.format}
            href={api.digestExportUrl(id, entry.format)}
            download
            title={entry.hint}
            className={linkButtonClass("secondary", "sm")}
          >
            {entry.label}
          </a>
        ))}
      </div>
    </div>
  );
}

function DeliveryPanel({ id }: { id: Id }) {
  const { channels: available, known, loading } = useDeliveryChannels();
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

  const needsTo =
    available.some((channel) => channel.needs_target && channels.includes(channel.id)) ||
    custom.trim() === "email";

  return (
    <div className="mt-5 border-t border-line pt-4">
      <h4 className="text-sm font-semibold">Deliver this digest</h4>
      <div className="mt-2 flex flex-wrap gap-2">
        {available.map((channel) => {
          const active = channels.includes(channel.id);
          return (
            <button
              key={channel.id}
              type="button"
              onClick={() => toggleChannel(channel.id)}
              aria-pressed={active}
              title={
                !known
                  ? `Whether ${channel.label} is configured here is not known yet.`
                  : channel.configured
                    ? `${channel.label} is ready.`
                    : `${channel.label} has no credential on this deployment, so a send to it would fail.`
              }
              className={cx(
                "rounded-full border px-3 py-1 text-sm font-medium transition-colors",
                active
                  ? "border-transparent bg-accent text-accent-ink"
                  : "border-line text-muted hover:text-ink",
                known && !channel.configured && !active && "opacity-55",
              )}
            >
              {channel.label}
              {known && !channel.configured ? " (not configured)" : ""}
            </button>
          );
        })}
      </div>
      {known ? null : <ChannelsUnknownNote loading={loading} />}

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
