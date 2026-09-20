"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import { useConfig } from "@/components/ConfigProvider";
import { Select } from "@/components/ui";
import { loadPrefs, savePrefs, subscribeToVault } from "@/lib/keys";
import type { ProviderStatus } from "@/lib/types";

export interface Selection {
  provider: string;
  model: string;
}

/** Read the stored provider and model, falling back to the first usable one. */
export function useProviderSelection(): {
  selection: Selection;
  setSelection: (next: Selection) => void;
  providers: ProviderStatus[];
  ready: boolean;
} {
  const { config } = useConfig();
  const [stored, setStored] = useState<Selection>({ provider: "", model: "" });
  const [ready, setReady] = useState(false);

  useEffect(() => {
    const read = () => {
      const prefs = loadPrefs();
      setStored({ provider: prefs.provider ?? "", model: prefs.model ?? "" });
      setReady(true);
    };
    read();
    return subscribeToVault(read);
  }, []);

  const providers = useMemo(
    () => (config?.providers ?? []).filter((provider) => provider.available),
    [config],
  );

  const selection = useMemo<Selection>(() => {
    const chosen = providers.find((provider) => provider.id === stored.provider) ?? providers[0];
    if (!chosen) return { provider: "", model: "" };
    const model =
      chosen.models.some((option) => option.id === stored.model) && stored.provider === chosen.id
        ? stored.model
        : chosen.default_model || chosen.models[0]?.id || "";
    return { provider: chosen.id, model };
  }, [providers, stored]);

  const setSelection = useCallback((next: Selection) => {
    setStored(next);
    savePrefs({ provider: next.provider, model: next.model });
  }, []);

  return { selection, setSelection, providers, ready };
}

/** Two selects, or a pointer to Settings when no provider can be used yet. */
export function ProviderPicker({
  selection,
  onChange,
  providers,
  compact = false,
}: {
  selection: Selection;
  onChange: (next: Selection) => void;
  providers: ProviderStatus[];
  compact?: boolean;
}) {
  const current = providers.find((provider) => provider.id === selection.provider);

  if (providers.length === 0) {
    return (
      <p className="text-sm text-muted">
        No AI provider is usable yet.{" "}
        <Link href="/settings" className="font-medium text-accent underline underline-offset-2">
          Add a key in Settings
        </Link>
        .
      </p>
    );
  }

  return (
    <div className={compact ? "flex flex-wrap gap-2" : "grid gap-3 sm:grid-cols-2"}>
      <label className="flex min-w-0 flex-1 flex-col gap-1">
        <span className="text-xs font-medium text-muted">Provider</span>
        <Select
          value={selection.provider}
          onChange={(event) => {
            const next = providers.find((provider) => provider.id === event.target.value);
            onChange({
              provider: event.target.value,
              model: next?.default_model || next?.models[0]?.id || "",
            });
          }}
        >
          {providers.map((provider) => (
            <option key={provider.id} value={provider.id}>
              {provider.label}
              {provider.local ? " (local)" : ""}
            </option>
          ))}
        </Select>
      </label>

      <label className="flex min-w-0 flex-1 flex-col gap-1">
        <span className="text-xs font-medium text-muted">Model</span>
        <Select
          value={selection.model}
          onChange={(event) => onChange({ provider: selection.provider, model: event.target.value })}
        >
          {(current?.models ?? []).map((model) => (
            <option key={model.id} value={model.id}>
              {model.label}
              {model.cheap ? " (cheap)" : ""}
            </option>
          ))}
          {current && current.models.length === 0 ? (
            <option value={selection.model}>{selection.model || "Default"}</option>
          ) : null}
        </Select>
      </label>
    </div>
  );
}
