"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, api, toApiError } from "./api";
import type { Run } from "./types";

const POLL_MS = 1500;
/** A guard against a run that pauses forever because a stage makes no progress. */
const MAX_AUTO_CONTINUES = 40;

export interface RunController {
  run: Run | null;
  error: ApiError | null;
  /** True while a request is in flight, as opposed to while a run is merely open. */
  busy: boolean;
  /** True while the run is pending, running or waiting to be continued. */
  active: boolean;
  autoContinue: boolean;
  setAutoContinue: (value: boolean) => void;
  /** How many times this client has resumed the current run after a pause. */
  continuations: number;
  start: (body?: { provider?: string; model?: string; stages?: string[] }) => void;
  resume: () => void;
  dismiss: () => void;
}

function isOpen(status: Run["status"]): boolean {
  return status === "pending" || status === "running" || status === "paused";
}

/**
 * Owns one workspace run from start to finish.
 *
 * A serverless deployment cannot keep working after it answers, so a long run
 * checkpoints itself and comes back as `paused`. That is the normal path, not
 * an error: the client posts to /continue and the run picks up where it left
 * off. This hook does that automatically unless the user turns it off, and it
 * always exposes the state so the UI can offer a manual Continue.
 */
export function useRun(slug: string, options: { onFinished?: () => void } = {}): RunController {
  const [run, setRun] = useState<Run | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);
  const [autoContinue, setAutoContinue] = useState(true);
  const [continuations, setContinuations] = useState(0);

  const timer = useRef<number | null>(null);
  const alive = useRef(true);
  const inFlight = useRef(false);
  const onFinished = useRef(options.onFinished);
  onFinished.current = options.onFinished;

  const autoRef = useRef(autoContinue);
  autoRef.current = autoContinue;
  const continuationsRef = useRef(0);

  const clearTimer = useCallback(() => {
    if (timer.current !== null) {
      window.clearTimeout(timer.current);
      timer.current = null;
    }
  }, []);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      clearTimer();
    };
  }, [clearTimer]);

  // A different workspace means a different run.
  useEffect(() => {
    clearTimer();
    setRun(null);
    setError(null);
    setBusy(false);
    setContinuations(0);
    continuationsRef.current = 0;
  }, [slug, clearTimer]);

  const settle = useCallback((next: Run) => {
    if (!alive.current) return;
    setRun(next);
    setError(null);
    if (!isOpen(next.status)) {
      onFinished.current?.();
    }
  }, []);

  const step = useCallback(
    async (current: Run) => {
      if (!alive.current || inFlight.current) return;

      if (current.status === "paused") {
        if (!autoRef.current || continuationsRef.current >= MAX_AUTO_CONTINUES) return;
        inFlight.current = true;
        continuationsRef.current += 1;
        setContinuations(continuationsRef.current);
        try {
          const next = await api.continueRun(current.id);
          settle(next);
        } catch (cause) {
          if (alive.current) setError(toApiError(cause));
        } finally {
          inFlight.current = false;
        }
        return;
      }

      if (current.status !== "pending" && current.status !== "running") return;

      inFlight.current = true;
      try {
        const next = await api.getRun(current.id);
        settle(next);
      } catch (cause) {
        if (alive.current) setError(toApiError(cause));
      } finally {
        inFlight.current = false;
      }
    },
    [settle],
  );

  // One scheduler, driven by whatever state the run is in right now.
  useEffect(() => {
    clearTimer();
    if (!run || !isOpen(run.status)) return;
    if (run.status === "paused" && (!autoContinue || continuations >= MAX_AUTO_CONTINUES)) return;

    timer.current = window.setTimeout(() => {
      void step(run);
    }, run.status === "paused" ? 250 : POLL_MS);

    return clearTimer;
  }, [run, autoContinue, continuations, step, clearTimer]);

  const start = useCallback(
    (body: { provider?: string; model?: string; stages?: string[] } = {}) => {
      if (busy) return;
      setBusy(true);
      setError(null);
      setContinuations(0);
      continuationsRef.current = 0;
      api
        .startRun(slug, body)
        .then((next) => settle(next))
        .catch((cause: unknown) => {
          if (alive.current) setError(toApiError(cause));
        })
        .finally(() => {
          if (alive.current) setBusy(false);
        });
    },
    [slug, busy, settle],
  );

  const resume = useCallback(() => {
    if (!run || busy) return;
    setBusy(true);
    setError(null);
    continuationsRef.current += 1;
    setContinuations(continuationsRef.current);
    api
      .continueRun(run.id)
      .then((next) => settle(next))
      .catch((cause: unknown) => {
        if (alive.current) setError(toApiError(cause));
      })
      .finally(() => {
        if (alive.current) setBusy(false);
      });
  }, [run, busy, settle]);

  const dismiss = useCallback(() => {
    clearTimer();
    setRun(null);
    setError(null);
  }, [clearTimer]);

  return {
    run,
    error,
    busy,
    active: Boolean(run && isOpen(run.status)),
    autoContinue,
    setAutoContinue,
    continuations,
    start,
    resume,
    dismiss,
  };
}

export { MAX_AUTO_CONTINUES };
export type { ApiError };
