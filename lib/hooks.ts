"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, toApiError } from "./api";

export interface AsyncState<T> {
  data: T | null;
  error: ApiError | null;
  loading: boolean;
  /** True only for the very first attempt, so a refresh does not flash a skeleton. */
  initialising: boolean;
  reload: () => void;
  setData: (next: T) => void;
}

/**
 * Run an async loader and keep its result, its error and its loading flag.
 *
 * Every screen in this app can fail, because the backend may have no provider
 * configured, no database or no network. The error is kept as an ApiError so
 * the caller can render the backend's own hint.
 */
export function useAsync<T>(
  loader: (signal: AbortSignal) => Promise<T>,
  deps: readonly unknown[],
): AsyncState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(true);
  const [initialising, setInitialising] = useState(true);
  const [nonce, setNonce] = useState(0);

  const loaderRef = useRef(loader);
  loaderRef.current = loader;

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    setLoading(true);

    loaderRef
      .current(controller.signal)
      .then((result) => {
        if (!active) return;
        setData(result);
        setError(null);
      })
      .catch((cause: unknown) => {
        if (!active) return;
        if (cause instanceof DOMException && cause.name === "AbortError") return;
        setError(toApiError(cause));
      })
      .finally(() => {
        if (!active) return;
        setLoading(false);
        setInitialising(false);
      });

    return () => {
      active = false;
      controller.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce]);

  const reload = useCallback(() => setNonce((value) => value + 1), []);

  return { data, error, loading, initialising, reload, setData };
}

/** True once the component has mounted, for anything that must not run on the server. */
export function useMounted(): boolean {
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  return mounted;
}

/** Debounce a value, for search boxes that would otherwise fire on every key. */
export function useDebounced<T>(value: T, delay = 300): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), delay);
    return () => window.clearTimeout(timer);
  }, [value, delay]);
  return debounced;
}
