/**
 * The one place the browser talks to the backend.
 *
 * Every request goes through `request()`, which is what makes the bring your
 * own key promise checkable: credentials are read from localStorage and
 * attached as headers here and nowhere else, they never appear in a URL, and
 * nothing in this file logs a request body or a header.
 */

import { credentialHeaders } from "./keys";
import type {
  AppConfig,
  ArticlesResponse,
  ChatBody,
  ChatResponse,
  CreateSourceBody,
  CreateWorkspaceBody,
  DeliverBody,
  DeliverResponse,
  Digest,
  DigestSummary,
  DiscoverResponse,
  EventsResponse,
  HealthResponse,
  Id,
  JsonObject,
  ResearchBackendsResponse,
  ResearchBody,
  ResearchResponse,
  ResearchSaveBody,
  Run,
  SearchResponse,
  Source,
  StartRunBody,
  UpdateSourceBody,
  UpdateWorkspaceBody,
  Usage,
  VerifyResponse,
  Workspace,
  WorkspaceDetail,
} from "./types";

/**
 * An error carrying the backend's own wording. `hint` is written to tell the
 * reader what to do next, so every error surface in the UI renders it.
 */
export class ApiError extends Error {
  readonly hint: string;
  readonly provider: string;
  readonly status: number;

  constructor(message: string, options: { hint?: string; provider?: string; status?: number } = {}) {
    super(message);
    this.name = "ApiError";
    this.hint = options.hint ?? "";
    this.provider = options.provider ?? "";
    this.status = options.status ?? 0;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function readErrorPayload(payload: unknown, status: number): ApiError {
  if (isRecord(payload) && isRecord(payload.error)) {
    const error = payload.error;
    const message = typeof error.message === "string" && error.message.trim()
      ? error.message
      : `Request failed with status ${status}.`;
    return new ApiError(message, {
      hint: typeof error.hint === "string" ? error.hint : "",
      provider: typeof error.provider === "string" ? error.provider : "",
      status,
    });
  }
  if (isRecord(payload) && typeof payload.detail === "string") {
    return new ApiError(payload.detail, { status });
  }
  return new ApiError(`Request failed with status ${status}.`, { status });
}

type QueryValue = string | number | boolean | undefined | null;

function withQuery(path: string, query?: Record<string, QueryValue>): string {
  if (!query) return path;
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === "") continue;
    params.set(key, String(value));
  }
  const qs = params.toString();
  return qs ? `${path}?${qs}` : path;
}

interface RequestOptions {
  method?: "GET" | "POST" | "PATCH" | "DELETE";
  body?: unknown;
  query?: Record<string, QueryValue>;
  signal?: AbortSignal;
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = "GET", body, query, signal } = options;

  const headers: Record<string, string> = {
    Accept: "application/json",
    ...credentialHeaders(),
  };
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
  }

  let response: Response;
  try {
    response = await fetch(withQuery(path, query), {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: "no-store",
      signal,
    });
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === "AbortError") {
      throw cause;
    }
    throw new ApiError("Could not reach the backend.", {
      hint:
        "The API did not answer. In development, start it with " +
        "`uvicorn server.main:app --reload --port 8000` and reload this page.",
    });
  }

  if (response.status === 204) {
    return undefined as T;
  }

  const text = await response.text();
  let payload: unknown = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = null;
    }
  }

  if (!response.ok) {
    if (payload === null && text) {
      throw new ApiError(`Request failed with status ${response.status}.`, {
        hint: "The backend replied with something that was not JSON.",
        status: response.status,
      });
    }
    throw readErrorPayload(payload, response.status);
  }

  return payload as T;
}

/* -------------------------------------------------------------------------- */
/* Endpoints                                                                   */
/* -------------------------------------------------------------------------- */

export const api = {
  health: (signal?: AbortSignal) => request<HealthResponse>("/api/health", { signal }),

  config: (signal?: AbortSignal) => request<AppConfig>("/api/config", { signal }),

  verifyProvider: (providerId: string, signal?: AbortSignal) =>
    request<VerifyResponse>(`/api/providers/${encodeURIComponent(providerId)}/verify`, {
      method: "POST",
      body: {},
      signal,
    }),

  listWorkspaces: (signal?: AbortSignal) => request<Workspace[]>("/api/workspaces", { signal }),

  createWorkspace: (body: CreateWorkspaceBody, signal?: AbortSignal) =>
    request<Workspace>("/api/workspaces", { method: "POST", body, signal }),

  getWorkspace: (slug: string, signal?: AbortSignal) =>
    request<WorkspaceDetail>(`/api/workspaces/${encodeURIComponent(slug)}`, { signal }),

  updateWorkspace: (slug: string, body: UpdateWorkspaceBody, signal?: AbortSignal) =>
    request<Workspace>(`/api/workspaces/${encodeURIComponent(slug)}`, {
      method: "PATCH",
      body,
      signal,
    }),

  deleteWorkspace: (slug: string, signal?: AbortSignal) =>
    request<void>(`/api/workspaces/${encodeURIComponent(slug)}`, { method: "DELETE", signal }),

  addSource: (slug: string, body: CreateSourceBody, signal?: AbortSignal) =>
    request<Source>(`/api/workspaces/${encodeURIComponent(slug)}/sources`, {
      method: "POST",
      body,
      signal,
    }),

  updateSource: (id: Id, body: UpdateSourceBody, signal?: AbortSignal) =>
    request<Source>(`/api/sources/${encodeURIComponent(String(id))}`, {
      method: "PATCH",
      body,
      signal,
    }),

  deleteSource: (id: Id, signal?: AbortSignal) =>
    request<void>(`/api/sources/${encodeURIComponent(String(id))}`, { method: "DELETE", signal }),

  discover: (slug: string, url: string, signal?: AbortSignal) =>
    request<DiscoverResponse>(`/api/workspaces/${encodeURIComponent(slug)}/discover`, {
      method: "POST",
      body: { url },
      signal,
    }),

  startRun: (slug: string, body: StartRunBody = {}, signal?: AbortSignal) =>
    request<Run>(`/api/workspaces/${encodeURIComponent(slug)}/run`, {
      method: "POST",
      body,
      signal,
    }),

  getRun: (id: Id, signal?: AbortSignal) =>
    request<Run>(`/api/runs/${encodeURIComponent(String(id))}`, { signal }),

  continueRun: (id: Id, signal?: AbortSignal) =>
    request<Run>(`/api/runs/${encodeURIComponent(String(id))}/continue`, {
      method: "POST",
      body: {},
      signal,
    }),

  events: (
    slug: string,
    query: { theme?: string; limit?: number; offset?: number } = {},
    signal?: AbortSignal,
  ) =>
    request<EventsResponse>(`/api/workspaces/${encodeURIComponent(slug)}/events`, {
      query,
      signal,
    }),

  articles: (
    slug: string,
    query: {
      theme?: string;
      q?: string;
      limit?: number;
      offset?: number;
      include_duplicates?: boolean;
    } = {},
    signal?: AbortSignal,
  ) =>
    request<ArticlesResponse>(`/api/workspaces/${encodeURIComponent(slug)}/articles`, {
      query,
      signal,
    }),

  search: (slug: string, q: string, limit?: number, signal?: AbortSignal) =>
    request<SearchResponse>(`/api/workspaces/${encodeURIComponent(slug)}/search`, {
      query: { q, limit },
      signal,
    }),

  listDigests: (slug: string, signal?: AbortSignal) =>
    request<DigestSummary[]>(`/api/workspaces/${encodeURIComponent(slug)}/digests`, { signal }),

  getDigest: (id: Id, signal?: AbortSignal) =>
    request<Digest>(`/api/digests/${encodeURIComponent(String(id))}`, { signal }),

  generateDigest: (slug: string, signal?: AbortSignal) =>
    request<Digest>(`/api/workspaces/${encodeURIComponent(slug)}/digest`, {
      method: "POST",
      body: {},
      signal,
    }),

  deliverDigest: (id: Id, body: DeliverBody, signal?: AbortSignal) =>
    request<DeliverResponse>(`/api/digests/${encodeURIComponent(String(id))}/deliver`, {
      method: "POST",
      body,
      signal,
    }),

  chat: (slug: string, body: ChatBody, signal?: AbortSignal) =>
    request<ChatResponse>(`/api/workspaces/${encodeURIComponent(slug)}/chat`, {
      method: "POST",
      body,
      signal,
    }),

  researchBackends: (signal?: AbortSignal) =>
    request<ResearchBackendsResponse>("/api/research/backends", { signal }),

  // No timeout is imposed here. A fan out across search engines, followed by
  // extraction and ranking, routinely runs into the tens of seconds, and the
  // caller cancels through the signal rather than losing a finished search.
  research: (body: ResearchBody, signal?: AbortSignal) =>
    request<ResearchResponse>("/api/research", { method: "POST", body, signal }),

  saveResearch: (body: ResearchSaveBody, signal?: AbortSignal) =>
    request<WorkspaceDetail>("/api/research/save", { method: "POST", body, signal }),

  usage: (slug: string, signal?: AbortSignal) =>
    request<Usage>(`/api/workspaces/${encodeURIComponent(slug)}/usage`, { signal }),

  feedUrl: (slug: string) => `/api/workspaces/${encodeURIComponent(slug)}/feed.xml`,
};

/** Narrow an unknown caught value into something with a message and a hint. */
export function toApiError(cause: unknown): ApiError {
  if (cause instanceof ApiError) return cause;
  if (cause instanceof Error) return new ApiError(cause.message);
  return new ApiError("Something went wrong.");
}

export type { JsonObject };
