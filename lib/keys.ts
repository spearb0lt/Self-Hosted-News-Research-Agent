/**
 * The browser side key vault.
 *
 * Provider credentials are kept in localStorage and nowhere else. They are
 * attached as request headers by lib/api.ts on their way to this app's own
 * /api routes, and they are never written to a URL, a query string, a log line
 * or any third party. Clearing the browser's site data removes them.
 */

import type { ArticleSort, EventSort, ResearchSort } from "./types";

const STORAGE_KEY = "news-research.provider-keys.v1";
const PREFS_KEY = "news-research.prefs.v1";
const VIEWS_KEY = "news-research.workspace-views.v1";
const EVENT_SORT_KEY = "news-research.event-sort.v1";
const ARTICLE_SORT_KEY = "news-research.article-sort.v1";
const RESEARCH_SORT_KEY = "news-research.research-sort.v1";

/** What the user pasted for one provider. */
export interface ProviderCredential {
  /** The API token. Sent as X-LLM-Key-{provider}. */
  key?: string;
  /** An override for the provider's base URL. Sent as X-LLM-Base-{provider}. */
  base?: string;
  /** Cloudflare needs an account id as well as a token. Sent as X-LLM-Account-{provider}. */
  account?: string;
}

export type KeyVault = Record<string, ProviderCredential>;

/** Preferences that only matter to the browser, kept beside the keys. */
export interface Prefs {
  provider?: string;
  model?: string;
  embedder?: string;
  autoContinue?: boolean;
}

const EMPTY_VAULT: KeyVault = {};

function canStore(): boolean {
  return typeof window !== "undefined" && typeof window.localStorage !== "undefined";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function readString(source: Record<string, unknown>, field: string): string | undefined {
  const value = source[field];
  if (typeof value !== "string") return undefined;
  const trimmed = value.trim();
  return trimmed ? trimmed : undefined;
}

/** Read the vault, tolerating anything that is not the shape we wrote. */
export function loadVault(): KeyVault {
  if (!canStore()) return EMPTY_VAULT;
  let raw: string | null = null;
  try {
    raw = window.localStorage.getItem(STORAGE_KEY);
  } catch {
    return EMPTY_VAULT;
  }
  if (!raw) return EMPTY_VAULT;

  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return EMPTY_VAULT;
  }
  if (!isRecord(parsed)) return EMPTY_VAULT;

  const vault: KeyVault = {};
  for (const [provider, value] of Object.entries(parsed)) {
    if (!isRecord(value)) continue;
    const entry: ProviderCredential = {};
    const key = readString(value, "key");
    const base = readString(value, "base");
    const account = readString(value, "account");
    if (key) entry.key = key;
    if (base) entry.base = base;
    if (account) entry.account = account;
    if (Object.keys(entry).length > 0) vault[provider] = entry;
  }
  return vault;
}

function writeVault(vault: KeyVault): void {
  if (!canStore()) return;
  try {
    const cleaned: KeyVault = {};
    for (const [provider, entry] of Object.entries(vault)) {
      if (entry.key || entry.base || entry.account) cleaned[provider] = entry;
    }
    if (Object.keys(cleaned).length === 0) {
      window.localStorage.removeItem(STORAGE_KEY);
    } else {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(cleaned));
    }
  } catch {
    // A full or blocked store is not worth breaking the page over. The key
    // simply will not survive a reload.
  }
  notify();
}

export function setCredential(provider: string, credential: ProviderCredential): void {
  const vault = loadVault();
  const entry: ProviderCredential = {};
  if (credential.key?.trim()) entry.key = credential.key.trim();
  if (credential.base?.trim()) entry.base = credential.base.trim();
  if (credential.account?.trim()) entry.account = credential.account.trim();
  if (Object.keys(entry).length === 0) {
    delete vault[provider];
  } else {
    vault[provider] = entry;
  }
  writeVault(vault);
}

export function clearCredential(provider: string): void {
  const vault = loadVault();
  delete vault[provider];
  writeVault(vault);
}

export function clearAllCredentials(): void {
  writeVault({});
}

/**
 * The headers the backend reads per request. Built fresh for every call so a
 * key pasted in another tab is picked up immediately.
 */
export function credentialHeaders(): Record<string, string> {
  const headers: Record<string, string> = {};
  for (const [provider, entry] of Object.entries(loadVault())) {
    if (entry.key) headers[`X-LLM-Key-${provider}`] = entry.key;
    if (entry.base) headers[`X-LLM-Base-${provider}`] = entry.base;
    if (entry.account) headers[`X-LLM-Account-${provider}`] = entry.account;
  }
  return headers;
}

/* -------------------------------------------------------------------------- */
/* Preferences                                                                 */
/* -------------------------------------------------------------------------- */

export function loadPrefs(): Prefs {
  if (!canStore()) return {};
  let raw: string | null = null;
  try {
    raw = window.localStorage.getItem(PREFS_KEY);
  } catch {
    return {};
  }
  if (!raw) return {};
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return {};
  }
  if (!isRecord(parsed)) return {};
  const prefs: Prefs = {};
  const provider = readString(parsed, "provider");
  const model = readString(parsed, "model");
  const embedder = readString(parsed, "embedder");
  if (provider) prefs.provider = provider;
  if (model) prefs.model = model;
  if (embedder) prefs.embedder = embedder;
  if (typeof parsed.autoContinue === "boolean") prefs.autoContinue = parsed.autoContinue;
  return prefs;
}

export function savePrefs(next: Prefs): void {
  if (!canStore()) return;
  const merged = { ...loadPrefs(), ...next };
  try {
    window.localStorage.setItem(PREFS_KEY, JSON.stringify(merged));
  } catch {
    // Same as above: a blocked store costs a preference, not the page.
  }
  notify();
}

/* -------------------------------------------------------------------------- */
/* Per workspace choices                                                       */
/* -------------------------------------------------------------------------- */

/** Which reading of a workspace the user last chose. */
export type WorkspaceView = "digest" | "raw";

type Guard<T> = (value: unknown) => value is T;

function isView(value: unknown): value is WorkspaceView {
  return value === "digest" || value === "raw";
}

function isEventSort(value: unknown): value is EventSort {
  return value === "importance" || value === "newest" || value === "oldest";
}

function isArticleSort(value: unknown): value is ArticleSort {
  return value === "newest" || value === "oldest";
}

function isResearchSort(value: unknown): value is ResearchSort {
  return value === "relevance" || value === "newest" || value === "oldest";
}

/**
 * Read one slug's entry out of a map of choices.
 *
 * Every value is put through a guard on the way out as well as on the way in,
 * because these keys outlive the code that wrote them: an option dropped in a
 * later version must read back as "no choice" rather than as itself.
 */
function loadPerSlug<T>(storageKey: string, slug: string, isValid: Guard<T>): T | null {
  if (!canStore()) return null;
  let raw: string | null = null;
  try {
    raw = window.localStorage.getItem(storageKey);
  } catch {
    return null;
  }
  if (!raw) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!isRecord(parsed)) return null;
  const value = parsed[slug];
  return isValid(value) ? value : null;
}

function savePerSlug<T>(storageKey: string, slug: string, value: T, isValid: Guard<T>): void {
  if (!canStore()) return;
  try {
    const raw = window.localStorage.getItem(storageKey);
    const parsed: unknown = raw ? JSON.parse(raw) : {};
    const kept: Record<string, T> = {};
    if (isRecord(parsed)) {
      for (const [key, entry] of Object.entries(parsed)) {
        if (isValid(entry)) kept[key] = entry;
      }
    }
    kept[slug] = value;
    window.localStorage.setItem(storageKey, JSON.stringify(kept));
  } catch {
    // Same as the vault: a blocked store costs a preference, not the page.
  }
}

/**
 * Kept per slug rather than as one global preference, because a workspace with
 * AI turned off is only readable as a raw list while one with it on is not.
 */
export function loadWorkspaceView(slug: string): WorkspaceView | null {
  return loadPerSlug(VIEWS_KEY, slug, isView);
}

export function saveWorkspaceView(slug: string, view: WorkspaceView): void {
  savePerSlug(VIEWS_KEY, slug, view, isView);
}

/**
 * Also per slug: a fast moving beat is read newest first, while a workspace
 * followed for its big stories is read by importance.
 */
export function loadEventSort(slug: string): EventSort | null {
  return loadPerSlug(EVENT_SORT_KEY, slug, isEventSort);
}

export function saveEventSort(slug: string, sort: EventSort): void {
  savePerSlug(EVENT_SORT_KEY, slug, sort, isEventSort);
}

export function loadArticleSort(slug: string): ArticleSort | null {
  return loadPerSlug(ARTICLE_SORT_KEY, slug, isArticleSort);
}

export function saveArticleSort(slug: string, sort: ArticleSort): void {
  savePerSlug(ARTICLE_SORT_KEY, slug, sort, isArticleSort);
}

/* -------------------------------------------------------------------------- */
/* Research                                                                    */
/* -------------------------------------------------------------------------- */

/**
 * One choice for the whole Research page, since a topic search belongs to no
 * workspace. It is kept out of Prefs so that changing it does not wake every
 * provider picker subscribed to the vault.
 */
export function loadResearchSort(): ResearchSort | null {
  if (!canStore()) return null;
  let raw: string | null = null;
  try {
    raw = window.localStorage.getItem(RESEARCH_SORT_KEY);
  } catch {
    return null;
  }
  return isResearchSort(raw) ? raw : null;
}

export function saveResearchSort(sort: ResearchSort): void {
  if (!canStore()) return;
  try {
    window.localStorage.setItem(RESEARCH_SORT_KEY, sort);
  } catch {
    // Same as the vault: a blocked store costs a preference, not the page.
  }
}

/* -------------------------------------------------------------------------- */
/* Change notification                                                         */
/* -------------------------------------------------------------------------- */

type Listener = () => void;
const listeners = new Set<Listener>();

function notify(): void {
  for (const listener of listeners) listener();
}

/** Subscribe to vault or preference changes, including from another tab. */
export function subscribeToVault(listener: Listener): () => void {
  listeners.add(listener);
  const onStorage = (event: StorageEvent) => {
    if (event.key === STORAGE_KEY || event.key === PREFS_KEY || event.key === null) {
      listener();
    }
  };
  if (typeof window !== "undefined") {
    window.addEventListener("storage", onStorage);
  }
  return () => {
    listeners.delete(listener);
    if (typeof window !== "undefined") {
      window.removeEventListener("storage", onStorage);
    }
  };
}

/** A redacted preview for the settings screen. The real value never leaves storage. */
export function maskKey(value: string): string {
  const trimmed = value.trim();
  if (trimmed.length <= 8) return "*".repeat(trimmed.length);
  return `${trimmed.slice(0, 4)}${"*".repeat(Math.min(16, trimmed.length - 8))}${trimmed.slice(-4)}`;
}
