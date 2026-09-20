"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { useConfig } from "@/components/ConfigProvider";
import { ErrorState, SkeletonLine } from "@/components/states";
import { Badge, Button, Card, TextArea, TextInput, cx } from "@/components/ui";
import { ApiError, api, toApiError } from "@/lib/api";
import { slugify } from "@/lib/format";
import type { Preset, Workspace } from "@/lib/types";

const BLANK = "__blank__";

function guessTimezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

/** Preset first, blank second. A preset arrives with themes and sources already set. */
export function CreateWorkspacePanel({
  onCreated,
  onCancel,
}: {
  onCreated: (workspace: Workspace) => void;
  onCancel: () => void;
}) {
  const { config, loading } = useConfig();
  const presets = useMemo<Preset[]>(() => config?.presets ?? [], [config]);

  const [choice, setChoice] = useState<string>(BLANK);
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [slugTouched, setSlugTouched] = useState(false);
  const [description, setDescription] = useState("");
  const [timezone, setTimezone] = useState("UTC");
  const [themes, setThemes] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    setTimezone(guessTimezone());
    headingRef.current?.focus();
  }, []);

  useEffect(() => {
    if (choice === BLANK) return;
    const preset = presets.find((item) => item.slug === choice);
    if (!preset) return;
    setName((current) => (current ? current : preset.name));
    setDescription((current) => (current ? current : preset.description));
    setThemes((current) => (current ? current : preset.themes.join(", ")));
  }, [choice, presets]);

  const effectiveSlug = slugTouched ? slug : slugify(name);

  const submit = () => {
    if (submitting) return;
    setSubmitting(true);
    setError(null);

    const themeList = themes
      .split(",")
      .map((entry) => entry.trim())
      .filter(Boolean);

    api
      .createWorkspace({
        preset: choice === BLANK ? undefined : choice,
        name: name.trim() || undefined,
        slug: effectiveSlug || undefined,
        description: description.trim() || undefined,
        timezone: timezone.trim() || undefined,
        themes: themeList.length ? themeList : undefined,
      })
      .then(onCreated)
      .catch((cause: unknown) => setError(toApiError(cause)))
      .finally(() => setSubmitting(false));
  };

  return (
    <Card className="p-4 sm:p-6">
      <h2
        ref={headingRef}
        tabIndex={-1}
        className="text-base font-semibold tracking-tight outline-none"
      >
        New workspace
      </h2>
      <p className="mt-1 text-sm text-muted">
        A workspace is one news beat: its own sources, themes, digest and archive.
      </p>

      <fieldset className="mt-5">
        <legend className="text-sm font-medium">Start from</legend>
        {loading && presets.length === 0 ? (
          <div className="mt-3 grid gap-2 sm:grid-cols-2">
            <SkeletonLine className="h-16 rounded-lg" />
            <SkeletonLine className="h-16 rounded-lg" />
          </div>
        ) : (
          <div className="mt-3 grid gap-2 sm:grid-cols-2">
            <PresetOption
              selected={choice === BLANK}
              onSelect={() => setChoice(BLANK)}
              title="Blank workspace"
              description="Start with nothing and add your own sources."
            />
            {presets.map((preset) => (
              <PresetOption
                key={preset.slug}
                selected={choice === preset.slug}
                onSelect={() => setChoice(preset.slug)}
                title={preset.name}
                description={preset.description}
                meta={
                  <>
                    {preset.sources ? (
                      <Badge tone="neutral">{preset.sources} sources</Badge>
                    ) : null}
                    {preset.themes.slice(0, 3).map((theme) => (
                      <Badge key={theme} tone="accent">
                        {theme}
                      </Badge>
                    ))}
                  </>
                }
              />
            ))}
          </div>
        )}
      </fieldset>

      <div className="mt-5 grid gap-4 sm:grid-cols-2">
        <TextInput
          label="Name"
          value={name}
          onChange={(event) => setName(event.target.value)}
          placeholder="Climate policy"
          autoComplete="off"
        />
        <TextInput
          label="Slug"
          value={effectiveSlug}
          onChange={(event) => {
            setSlugTouched(true);
            setSlug(slugify(event.target.value));
          }}
          placeholder="climate-policy"
          hint="Used in the URL and the RSS feed."
          autoComplete="off"
        />
        <TextInput
          label="Time zone"
          value={timezone}
          onChange={(event) => setTimezone(event.target.value)}
          placeholder="UTC"
          hint="Decides when a day starts for the digest."
          autoComplete="off"
        />
        <TextInput
          label="Themes"
          value={themes}
          onChange={(event) => setThemes(event.target.value)}
          placeholder="Policy, Markets, Technology"
          hint="Comma separated. Stories get sorted into these."
          autoComplete="off"
        />
        <TextArea
          label="Description"
          className="sm:col-span-2"
          rows={2}
          value={description}
          onChange={(event) => setDescription(event.target.value)}
          placeholder="What this beat covers."
        />
      </div>

      {error ? (
        <div className="mt-4">
          <ErrorState error={error} compact />
        </div>
      ) : null}

      <div className="mt-5 flex flex-wrap gap-2">
        <Button variant="primary" onClick={submit} loading={submitting}>
          Create workspace
        </Button>
        <Button variant="ghost" onClick={onCancel} disabled={submitting}>
          Cancel
        </Button>
      </div>
    </Card>
  );
}

function PresetOption({
  selected,
  onSelect,
  title,
  description,
  meta,
}: {
  selected: boolean;
  onSelect: () => void;
  title: string;
  description: string;
  meta?: React.ReactNode;
}) {
  return (
    <label
      className={cx(
        "flex cursor-pointer flex-col gap-1 rounded-lg border p-3 transition-colors",
        selected ? "border-accent bg-accent-soft" : "border-line hover:border-line-strong",
      )}
    >
      <span className="flex items-start gap-2">
        <input
          type="radio"
          name="workspace-preset"
          checked={selected}
          onChange={onSelect}
          className="mt-1 size-4 shrink-0 accent-[var(--accent)]"
        />
        <span className="min-w-0">
          <span className="block text-sm font-medium">{title}</span>
          {description ? (
            <span className="block text-xs text-muted break-anywhere">{description}</span>
          ) : null}
        </span>
      </span>
      {meta ? <span className="flex flex-wrap gap-1 pl-6">{meta}</span> : null}
    </label>
  );
}
