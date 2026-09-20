"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { ApiError, api, toApiError } from "@/lib/api";
import { subscribeToVault } from "@/lib/keys";
import type { AppConfig } from "@/lib/types";

interface ConfigContextValue {
  config: AppConfig | null;
  error: ApiError | null;
  loading: boolean;
  reload: () => void;
}

const ConfigContext = createContext<ConfigContextValue>({
  config: null,
  error: null,
  loading: true,
  reload: () => {},
});

/**
 * /api/config describes what this deployment can do, which provider keys it
 * already has and what source kinds exist. Almost every screen needs some of
 * it, so it is fetched once and shared, and refetched whenever the key vault
 * changes because key_source depends on the headers we send.
 */
export function ConfigProvider({ children }: { children: ReactNode }) {
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(true);
  const [nonce, setNonce] = useState(0);
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    api
      .config(controller.signal)
      .then((next) => {
        if (!alive.current) return;
        setConfig(next);
        setError(null);
      })
      .catch((cause: unknown) => {
        if (!alive.current) return;
        if (cause instanceof DOMException && cause.name === "AbortError") return;
        setError(toApiError(cause));
      })
      .finally(() => {
        if (alive.current) setLoading(false);
      });
    return () => controller.abort();
  }, [nonce]);

  const reload = useCallback(() => setNonce((value) => value + 1), []);

  // A key pasted on the settings page changes what /api/config reports.
  useEffect(() => subscribeToVault(reload), [reload]);

  const value = useMemo<ConfigContextValue>(
    () => ({ config, error, loading, reload }),
    [config, error, loading, reload],
  );

  return <ConfigContext.Provider value={value}>{children}</ConfigContext.Provider>;
}

export function useConfig(): ConfigContextValue {
  return useContext(ConfigContext);
}
