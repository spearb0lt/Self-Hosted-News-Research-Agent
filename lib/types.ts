/**
 * TypeScript mirror of the FastAPI contract served under /api.
 *
 * Every shape here matches a response the backend actually returns. Nothing in
 * the UI should invent a field that is not in this file.
 */

/** Primary keys come back as integers today, but nothing in the UI depends on that. */
export type Id = string | number;

/** Any JSON value the backend passes through without a fixed shape. */
export type JsonValue =
  | string
  | number
  | boolean
  | null
  | JsonValue[]
  | { [key: string]: JsonValue };

export type JsonObject = { [key: string]: JsonValue };

/* -------------------------------------------------------------------------- */
/* Runtime and configuration                                                   */
/* -------------------------------------------------------------------------- */

/** One thing this deployment may or may not be able to do. */
export interface Capability {
  id: string;
  label: string;
  available: boolean;
  /** Written to be shown to a person verbatim. Never paraphrase it. */
  reason: string;
}

export interface RuntimeInfo {
  tier: string;
  platform: string;
  max_job_seconds: number;
  capabilities: Capability[];
}

export interface HealthResponse {
  status: string;
  runtime: RuntimeInfo;
  providers_available: boolean;
}

export interface ModelOption {
  id: string;
  label: string;
  note: string;
  cheap: boolean;
}

/** Where the key being used for a provider came from. */
export type KeySource = "server" | "client" | "" | null;

export interface ProviderStatus {
  id: string;
  label: string;
  available: boolean;
  reason: string;
  models: ModelOption[];
  default_model: string;
  docs_url: string;
  key_names: string[];
  key_source: KeySource;
  /** True when the provider needs an account id as well as a token. */
  needs_account: boolean;
  has_account: boolean;
  /** True for providers that run on the user's own machine. */
  local: boolean;
}

export interface SourceKind {
  kind: string;
  label: string;
  url_hint: string;
  /** True when this kind is configured without a URL. */
  urlless: boolean;
  key_names: string[];
  available: boolean;
  reason: string;
}

export interface Preset {
  slug: string;
  name: string;
  description: string;
  /** Theme labels, not ids. A preset is a template, not a saved taxonomy. */
  themes: string[];
  /** How many sources the preset brings with it. */
  sources: number;
}

export interface AppConfig {
  app: {
    name: string;
    tagline: string;
  };
  runtime: RuntimeInfo;
  allow_client_keys: boolean;
  providers: ProviderStatus[];
  embedders: ProviderStatus[];
  source_kinds: SourceKind[];
  presets: Preset[];
}

export interface VerifyResponse {
  ok: boolean;
  models: string[];
}

/* -------------------------------------------------------------------------- */
/* Workspaces and sources                                                      */
/* -------------------------------------------------------------------------- */

/**
 * One bucket in a workspace's taxonomy.
 *
 * `id` is what an article or event carries in its own `theme` field, `label`
 * is the only thing a reader should ever see, and `hint` is the sentence the
 * classifier is given to decide what belongs here. The hint is load bearing:
 * editing a taxonomy must carry it through rather than drop it.
 */
export interface Theme {
  id: string;
  label: string;
  hint: string;
}

export interface Workspace {
  id: Id;
  slug: string;
  name: string;
  description: string;
  timezone: string;
  themes: Theme[];
  enabled: boolean;
  source_count: number;
  article_count: number;
  last_run_at: string | null;
}

export interface Source {
  id: Id;
  kind: string;
  name: string;
  url: string;
  config: JsonObject;
  weight: number;
  enabled: boolean;
  last_run_at: string | null;
  last_success_at: string | null;
  last_status: string | null;
  last_error: string | null;
  consecutive_failures: number;
  items_seen: number;
}

/**
 * The detail endpoint answers with the sources attached and the last run, and
 * leaves out the roll up counts the list endpoint carries, so those are
 * optional here and the UI falls back to what it can count itself.
 */
export interface WorkspaceDetail extends Omit<Workspace, "source_count" | "article_count" | "last_run_at"> {
  sources: Source[];
  source_count?: number;
  article_count?: number;
  last_run_at?: string | null;
  latest_run?: Run | null;
}

/** Creating accepts plain labels; the backend gives each one an id and an empty hint. */
export interface CreateWorkspaceBody {
  preset?: string;
  slug?: string;
  name?: string;
  description?: string;
  timezone?: string;
  themes?: Array<string | Theme>;
  keywords?: string[];
}

/** Updating accepts either shape, but sending Theme[] is what keeps the hints. */
export interface UpdateWorkspaceBody {
  name?: string;
  description?: string;
  timezone?: string;
  themes?: Array<string | Theme>;
  enabled?: boolean;
}

export interface CreateSourceBody {
  kind: string;
  url: string;
  name?: string;
  config?: JsonObject;
}

export interface UpdateSourceBody {
  name?: string;
  url?: string;
  enabled?: boolean;
  weight?: number;
  config?: JsonObject;
}

export interface DiscoveredFeed {
  url: string;
  title: string;
  kind: string;
}

export interface DiscoverResponse {
  feeds: DiscoveredFeed[];
}

/* -------------------------------------------------------------------------- */
/* Runs                                                                        */
/* -------------------------------------------------------------------------- */

/**
 * `paused` is not a failure. It means the serverless time budget ran out part
 * way through, the run checkpointed itself, and POSTing to
 * /api/runs/{id}/continue picks it up where it stopped.
 */
export type RunStatus = "pending" | "running" | "paused" | "done" | "failed";

export interface RunProgress {
  done: number;
  total: number;
}

export interface Run {
  id: Id;
  status: RunStatus;
  stage: string;
  stats: JsonObject;
  error: string | null;
  started_at: string | null;
  updated_at: string | null;
  finished_at: string | null;
  progress: RunProgress;
}

export interface StartRunBody {
  provider?: string;
  model?: string;
  stages?: string[];
}

/* -------------------------------------------------------------------------- */
/* Content                                                                     */
/* -------------------------------------------------------------------------- */

export interface Article {
  id: Id;
  url: string;
  title: string;
  summary: string;
  domain: string;
  author: string | null;
  published_at: string | null;
  theme: string | null;
  sentiment: string | null;
  importance: number | null;
  image_url: string | null;
  word_count: number | null;
}

export interface NewsEvent {
  id: Id;
  title: string;
  theme: string | null;
  summary: string;
  importance: number | null;
  size: number;
  source_count: number;
  first_seen: string | null;
  last_seen: string | null;
  articles: Article[];
}

export interface EventsResponse {
  events: NewsEvent[];
  total: number;
}

export interface ArticlesResponse {
  articles: Article[];
  total: number;
}

export interface ScoredArticle extends Article {
  score: number;
}

export interface SearchResponse {
  results: ScoredArticle[];
}

/* -------------------------------------------------------------------------- */
/* Digests and delivery                                                        */
/* -------------------------------------------------------------------------- */

export interface DigestSummary {
  id: Id;
  slug: string;
  title: string;
  period_start: string | null;
  period_end: string | null;
  created_at: string | null;
}

export interface Digest extends DigestSummary {
  markdown: string;
  html: string;
  payload: JsonObject;
}

export interface DeliverBody {
  channels: string[];
  to?: string;
}

export interface DeliveryResult {
  channel: string;
  status: string;
  error: string | null;
}

export interface DeliverResponse {
  results: DeliveryResult[];
}

/* -------------------------------------------------------------------------- */
/* Chat                                                                        */
/* -------------------------------------------------------------------------- */

export interface Citation {
  article_id: Id;
  title: string;
  url: string;
  domain: string;
  snippet: string;
}

export interface ChatBody {
  question: string;
  provider?: string;
  model?: string;
}

export interface ChatResponse {
  answer: string;
  citations: Citation[];
}

/* -------------------------------------------------------------------------- */
/* Usage                                                                       */
/* -------------------------------------------------------------------------- */

export interface UsageRow {
  provider: string;
  model: string;
  kind: string;
  calls: number;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
}

/**
 * The contract leaves the shape of recent_runs open, so every field past the id
 * is treated as optional and the table renders only what arrived.
 */
export interface UsageRun {
  id: Id;
  status?: string;
  stage?: string;
  started_at?: string | null;
  finished_at?: string | null;
  cost_usd?: number;
  provider?: string;
  model?: string;
}

export interface Usage {
  total_cost_usd: number;
  by_provider: UsageRow[];
  recent_runs: UsageRun[];
}

/* -------------------------------------------------------------------------- */
/* Errors                                                                      */
/* -------------------------------------------------------------------------- */

export interface ApiErrorPayload {
  error: {
    message: string;
    hint: string;
    provider: string;
  };
}
