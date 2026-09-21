"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { useConfig } from "@/components/ConfigProvider";
import { ErrorState, SkeletonCards } from "@/components/states";
import {
  Badge,
  Button,
  Card,
  Notice,
  SectionHeading,
  Select,
  TextInput,
  Toggle,
  cx,
} from "@/components/ui";
import { ApiError, api, toApiError } from "@/lib/api";
import { useDisplayPrefs } from "@/lib/hooks";
import {
  clearAllCredentials,
  clearCredential,
  loadPrefs,
  loadVault,
  maskKey,
  savePrefs,
  saveDisplayPrefs,
  setCredential,
  subscribeToVault,
  type ProviderCredential,
} from "@/lib/keys";
import type { Capability, ProviderStatus } from "@/lib/types";

export default function GlobalSettingsPage() {
  const { config, error, loading, reload } = useConfig();

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">Settings</h1>
        <p className="mt-1 max-w-prose text-sm text-muted">
          Provider keys, the embedding model, what you want shown as you read, and what this
          deployment can and cannot do.
        </p>
      </div>

      {/* Outside everything below, because what a reader wants rendered is kept
          in this browser and needs no backend. Gating it on /api/config would
          mean losing the switches whenever the API is slow or down, which is
          exactly when someone is most likely to be fiddling with settings. */}
      <DisplayCard />

      {loading && !config ? (
        <SkeletonCards count={3} lines={4} />
      ) : error && !config ? (
        <ErrorState error={error} onRetry={reload} title="Could not load the configuration" />
      ) : config ? (
        <>
          <KeyVaultCard providers={config.providers} allowClientKeys={config.allow_client_keys} />
          <EmbedderCard embedders={config.embedders} />
          <CapabilitiesCard
            capabilities={config.runtime.capabilities}
            tier={config.runtime.tier}
            platform={config.runtime.platform}
            maxJobSeconds={config.runtime.max_job_seconds}
          />
        </>
      ) : null}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Keys                                                                        */
/* -------------------------------------------------------------------------- */

function KeyVaultCard({
  providers,
  allowClientKeys,
}: {
  providers: ProviderStatus[];
  allowClientKeys: boolean;
}) {
  const [showAll, setShowAll] = useState(false);
  const visible = useMemo(() => {
    if (showAll) return providers;
    // Anything already usable stays in view; the long tail is behind a toggle.
    const interesting = providers.filter(
      (provider) => provider.available || provider.key_source || provider.local,
    );
    return interesting.length > 0 ? interesting : providers.slice(0, 6);
  }, [providers, showAll]);

  return (
    <Card className="p-4 sm:p-6">
      <SectionHeading
        title="AI provider keys"
        description="Keys are stored in this browser only. They are attached to your own requests as headers and are never written to a URL, logged, or shared."
        action={
          <Button
            size="sm"
            variant="ghost"
            onClick={() => {
              clearAllCredentials();
            }}
          >
            Clear all keys
          </Button>
        }
      />

      {!allowClientKeys ? (
        <div className="mb-4">
          <Notice tone="warn" title="This deployment ignores keys from the browser">
            Pasting a key here will have no effect. The server decides which providers are
            available.
          </Notice>
        </div>
      ) : null}

      <ul className="flex flex-col gap-3">
        {visible.map((provider) => (
          <ProviderCard key={provider.id} provider={provider} />
        ))}
      </ul>

      {providers.length > visible.length || showAll ? (
        <div className="mt-4">
          <Button size="sm" variant="secondary" onClick={() => setShowAll((value) => !value)}>
            {showAll ? "Show fewer providers" : `Show all ${providers.length} providers`}
          </Button>
        </div>
      ) : null}
    </Card>
  );
}

function ProviderCard({ provider }: { provider: ProviderStatus }) {
  const [stored, setStored] = useState<ProviderCredential>({});
  const [key, setKey] = useState("");
  const [base, setBase] = useState("");
  const [account, setAccount] = useState("");
  const [open, setOpen] = useState(false);
  const [verifying, setVerifying] = useState(false);
  const [verifyError, setVerifyError] = useState<ApiError | null>(null);
  const [verified, setVerified] = useState<string[] | null>(null);

  const read = useCallback(() => {
    const entry = loadVault()[provider.id] ?? {};
    setStored(entry);
    setBase(entry.base ?? "");
    setAccount(entry.account ?? "");
  }, [provider.id]);

  useEffect(() => {
    read();
    return subscribeToVault(read);
  }, [read]);

  const save = () => {
    setCredential(provider.id, {
      key: key.trim() || stored.key,
      base: base.trim(),
      account: account.trim(),
    });
    setKey("");
    setVerified(null);
    setVerifyError(null);
  };

  const forget = () => {
    clearCredential(provider.id);
    setKey("");
    setBase("");
    setAccount("");
    setVerified(null);
    setVerifyError(null);
  };

  const verify = () => {
    setVerifying(true);
    setVerifyError(null);
    setVerified(null);
    api
      .verifyProvider(provider.id)
      .then((response) => {
        if (response.ok) setVerified(response.models);
        else
          setVerifyError(
            new ApiError(`${provider.label} did not accept that key.`, {
              hint: "Check the key was copied whole, and that the account it belongs to is active.",
              provider: provider.id,
            }),
          );
      })
      .catch((cause: unknown) => setVerifyError(toApiError(cause)))
      .finally(() => setVerifying(false));
  };

  const sourceBadge =
    provider.key_source === "server" ? (
      <Badge tone="ok" title="A key is configured on the server">
        server key
      </Badge>
    ) : provider.key_source === "client" ? (
      <Badge tone="accent" title="Using the key stored in this browser">
        your key
      </Badge>
    ) : provider.local ? (
      <Badge tone="neutral">no key needed</Badge>
    ) : (
      <Badge tone="neutral">no key</Badge>
    );

  return (
    <li className="rounded-xl border border-line p-3 sm:p-4">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="mr-auto min-w-0 text-sm font-semibold break-anywhere">{provider.label}</h3>
        {sourceBadge}
        <Badge tone={provider.available ? "ok" : "neutral"}>
          {provider.available ? "ready" : "unavailable"}
        </Badge>
        <Button size="sm" variant="ghost" onClick={() => setOpen((value) => !value)} aria-expanded={open}>
          {open ? "Close" : stored.key ? "Edit key" : "Add key"}
        </Button>
      </div>

      {!provider.available && provider.reason ? (
        <p className="mt-2 text-sm text-muted break-anywhere">{provider.reason}</p>
      ) : null}

      {stored.key ? (
        <p className="mt-2 font-mono text-xs text-muted break-anywhere">{maskKey(stored.key)}</p>
      ) : null}

      {open ? (
        <div className="mt-3 flex flex-col gap-3 rounded-lg bg-raised p-3">
          <TextInput
            label="API key"
            type="password"
            value={key}
            onChange={(event) => setKey(event.target.value)}
            placeholder={stored.key ? "Stored. Type a new key to replace it." : "Paste the key"}
            autoComplete="off"
            spellCheck={false}
            hint={
              provider.key_names.length
                ? `The server would read this from ${provider.key_names.join(" or ")}.`
                : undefined
            }
          />

          {provider.needs_account ? (
            <TextInput
              label="Account id"
              value={account}
              onChange={(event) => setAccount(event.target.value)}
              placeholder="Required alongside the token"
              autoComplete="off"
              spellCheck={false}
              hint={provider.has_account ? "The server already has one." : undefined}
            />
          ) : null}

          <TextInput
            label="Base URL"
            value={base}
            onChange={(event) => setBase(event.target.value)}
            placeholder="Optional. Leave empty for the provider's own endpoint."
            autoComplete="off"
            spellCheck={false}
          />

          <div className="flex flex-wrap gap-2">
            <Button variant="primary" size="sm" onClick={save}>
              Save in this browser
            </Button>
            <Button variant="secondary" size="sm" onClick={verify} loading={verifying}>
              Verify
            </Button>
            {stored.key || stored.base || stored.account ? (
              <Button variant="ghost" size="sm" onClick={forget}>
                Forget
              </Button>
            ) : null}
            {provider.docs_url ? (
              <a
                href={provider.docs_url}
                target="_blank"
                rel="noopener noreferrer"
                className="self-center rounded-md px-1 text-sm text-accent underline underline-offset-2"
              >
                Where to get a key
              </a>
            ) : null}
          </div>

          {verifyError ? <ErrorState error={verifyError} compact /> : null}

          {verified ? (
            <Notice tone="ok" title="The key works">
              {verified.length
                ? `${verified.length} models are reachable, including ${verified.slice(0, 3).join(", ")}.`
                : "The provider answered but listed no models."}
            </Notice>
          ) : null}
        </div>
      ) : null}
    </li>
  );
}

/* -------------------------------------------------------------------------- */
/* Embedder                                                                    */
/* -------------------------------------------------------------------------- */

function EmbedderCard({ embedders }: { embedders: ProviderStatus[] }) {
  const [choice, setChoice] = useState("");

  useEffect(() => {
    const read = () => setChoice(loadPrefs().embedder ?? "");
    read();
    return subscribeToVault(read);
  }, []);

  const selected = embedders.find((entry) => entry.id === choice) ?? null;

  return (
    <Card className="p-4 sm:p-6">
      <SectionHeading
        title="Embeddings"
        description="Embeddings power grouping stories into events and search by meaning."
      />

      {embedders.length === 0 ? (
        <Notice tone="warn" title="No embedding provider is available here">
          Search by meaning and event grouping fall back to matching words until one is
          configured.
        </Notice>
      ) : (
        <div className="flex flex-col gap-3">
          <Select
            label="Preferred embedding provider"
            value={choice}
            onChange={(event) => {
              setChoice(event.target.value);
              savePrefs({ embedder: event.target.value });
            }}
            hint="Kept in this browser. A key for it goes in the list above."
          >
            <option value="">Let the backend choose</option>
            {embedders.map((embedder) => (
              <option key={embedder.id} value={embedder.id} disabled={!embedder.available}>
                {embedder.label}
                {embedder.available ? "" : " (unavailable here)"}
              </option>
            ))}
          </Select>

          {selected && !selected.available && selected.reason ? (
            <Notice tone="warn" title={`${selected.label} cannot be used here`}>
              {selected.reason}
            </Notice>
          ) : null}

          <ul className="flex flex-wrap gap-2">
            {embedders.map((embedder) => (
              <li key={embedder.id}>
                <Badge tone={embedder.available ? "ok" : "neutral"}>
                  {embedder.label}
                  {embedder.local ? " (local)" : ""}
                </Badge>
              </li>
            ))}
          </ul>
        </div>
      )}
    </Card>
  );
}

/* -------------------------------------------------------------------------- */
/* What gets shown                                                             */
/* -------------------------------------------------------------------------- */

/**
 * A run computes more about a story than most readers want on the page at
 * once. These switches decide what is rendered and nothing else: no run
 * behaves differently, and no request is made, so they are kept in this
 * browser rather than on the workspace.
 */
function DisplayCard() {
  const prefs = useDisplayPrefs();

  return (
    <Card className="p-4 sm:p-6">
      <SectionHeading
        title="What to show as you read"
        description="Everything the pipeline already worked out, switched on or off for this browser. Nothing here changes what a run does or costs."
      />

      <div className="flex flex-col gap-4">
        <Toggle
          checked={prefs.eventCommentary}
          onChange={(value) => saveDisplayPrefs({ eventCommentary: value })}
          label="Why an event matters, and where the accounts differ"
          description={
            "Two lines the model writes for each event: one on what is at stake, one on where " +
            "the outlets do not tell the same story. Both are often empty, and an empty one is " +
            "never shown."
          }
        />
        <Toggle
          checked={prefs.contested}
          onChange={(value) => saveDisplayPrefs({ contested: value })}
          label="Flag events the outlets told differently"
          description={
            "Marks an event whose outlets differed in tone by 0.8 or more, and lets you open " +
            "which outlets carried it and how far apart they were. Grouping the same story " +
            "across mastheads is what makes this comparison possible at all."
          }
        />
        <Toggle
          checked={prefs.articleTone}
          onChange={(value) => saveDisplayPrefs({ articleTone: value })}
          label="Tone and stance on each article"
          description={
            "Whether an article reads negative, neutral or positive, and whether it reports, " +
            "argues for its subject, argues against it, or does both. Only articles that have " +
            "been through enrichment carry either."
          }
        />
        <Toggle
          checked={prefs.entities}
          onChange={(value) => saveDisplayPrefs({ entities: value })}
          label="The most covered people, organisations and places"
          description={
            "A panel on the digest ranking the names that came up most in a window, filterable " +
            "by kind. Empty for a workspace whose runs have not enriched anything yet."
          }
        />
      </div>
    </Card>
  );
}

/* -------------------------------------------------------------------------- */
/* Capabilities                                                                */
/* -------------------------------------------------------------------------- */

function CapabilitiesCard({
  capabilities,
  tier,
  platform,
  maxJobSeconds,
}: {
  capabilities: Capability[];
  tier: string;
  platform: string;
  maxJobSeconds: number;
}) {
  const unavailable = capabilities.filter((capability) => !capability.available);

  return (
    <Card className="p-4 sm:p-6">
      <SectionHeading
        title="What this deployment can do"
        description="The same code runs on a laptop and on a serverless host. Anything missing here is missing because of where it is running, and the reason below is the backend's own."
      />

      <dl className="mb-5 grid grid-cols-2 gap-3 sm:grid-cols-3">
        <div className="rounded-lg bg-raised p-3">
          <dt className="text-xs text-muted">Tier</dt>
          <dd className="mt-0.5 text-sm font-semibold break-anywhere">{tier}</dd>
        </div>
        <div className="rounded-lg bg-raised p-3">
          <dt className="text-xs text-muted">Platform</dt>
          <dd className="mt-0.5 text-sm font-semibold break-anywhere">{platform}</dd>
        </div>
        <div className="rounded-lg bg-raised p-3">
          <dt className="text-xs text-muted">Time per invocation</dt>
          <dd className="mt-0.5 text-sm font-semibold">{maxJobSeconds}s</dd>
        </div>
      </dl>

      {unavailable.length === 0 ? (
        <Notice tone="ok" title="Everything is available here">
          No feature is held back by this deployment.
        </Notice>
      ) : null}

      <ul className="flex flex-col gap-2">
        {capabilities.map((capability) => (
          <li
            key={capability.id}
            className={cx(
              "rounded-lg border p-3",
              capability.available ? "border-line" : "border-transparent bg-warn-soft",
            )}
          >
            <div className="flex flex-wrap items-center gap-2">
              <span className="mr-auto min-w-0 text-sm font-medium break-anywhere">
                {capability.label}
              </span>
              <Badge tone={capability.available ? "ok" : "warn"}>
                {capability.available ? "available" : "not here"}
              </Badge>
            </div>
            {!capability.available && capability.reason ? (
              // Rendered word for word. These strings are written for readers.
              <p className="mt-1.5 text-sm text-ink/85 break-anywhere">{capability.reason}</p>
            ) : null}
          </li>
        ))}
      </ul>
    </Card>
  );
}
