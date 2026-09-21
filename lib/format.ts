/** Small display helpers shared by the pages. */

import type { ArticleStance, JsonValue, Theme } from "./types";

/**
 * Articles and events carry a theme id, while the taxonomy carries the label a
 * reader should see. An id with no matching theme still has to render, so the
 * raw id is the fallback rather than a blank.
 */
export function themeLabel(
  themes: readonly Theme[] | undefined,
  id: string | null | undefined,
): string {
  if (!id) return "";
  const match = themes?.find((theme) => theme.id === id);
  return match?.label || id;
}

/* -------------------------------------------------------------------------- */
/* Tone                                                                        */
/* -------------------------------------------------------------------------- */

export type SentimentBand = "negative" | "neutral" | "positive";

/**
 * Turn the stored score into one of three words.
 *
 * A bare float tells a reader nothing, and the scores this pipeline writes are
 * quantised to a tenth, so the band either side of zero is drawn just inside
 * that step: 0.0 is neutral, 0.1 already reads as positive.
 */
export function sentimentBand(value: number | null | undefined): SentimentBand | null {
  if (typeof value !== "number" || !Number.isFinite(value)) return null;
  if (value <= -0.05) return "negative";
  if (value >= 0.05) return "positive";
  return "neutral";
}

/** What each stance means, for a label a reader has not met before. */
const STANCE_HINTS: Record<ArticleStance, string> = {
  factual: "Reports what happened without arguing a position.",
  critical: "Argues against its subject.",
  supportive: "Argues for its subject.",
  mixed: "Argues both ways, or quotes two sides without settling it.",
};

export function stanceHint(stance: string | null | undefined): string {
  if (!stance) return "";
  return STANCE_HINTS[stance as ArticleStance] ?? "";
}

/** "4 minutes ago" for anything recent, an absolute date once it is old. */
export function relativeTime(value: string | null | undefined): string {
  if (!value) return "never";
  const then = Date.parse(value);
  if (Number.isNaN(then)) return "unknown";

  const seconds = Math.round((Date.now() - then) / 1000);
  const future = seconds < 0;
  const abs = Math.abs(seconds);

  if (abs < 45) return future ? "in a moment" : "just now";
  if (abs < 90) return future ? "in a minute" : "a minute ago";

  const minutes = Math.round(abs / 60);
  if (minutes < 60) return future
      ? `in ${plural(minutes, "minute")}`
      : `${plural(minutes, "minute")} ago`;

  const hours = Math.round(minutes / 60);
  if (hours < 24) return future
      ? `in ${plural(hours, "hour")}`
      : `${plural(hours, "hour")} ago`;

  const days = Math.round(hours / 24);
  if (days < 7) return future
      ? `in ${plural(days, "day")}`
      : `${plural(days, "day")} ago`;

  return absoluteDate(value);
}

export function absoluteDate(value: string | null | undefined): string {
  if (!value) return "";
  const stamp = Date.parse(value);
  if (Number.isNaN(stamp)) return value;
  return new Date(stamp).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

export function absoluteDateTime(value: string | null | undefined): string {
  if (!value) return "";
  const stamp = Date.parse(value);
  if (Number.isNaN(stamp)) return value;
  return new Date(stamp).toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** Costs are small, so cents would round everything to zero. */
export function money(value: number | null | undefined): string {
  const amount = typeof value === "number" && Number.isFinite(value) ? value : 0;
  if (amount === 0) return "$0.00";
  if (amount < 0.01) return `$${amount.toFixed(4)}`;
  return `$${amount.toFixed(2)}`;
}

/**
 * A cost, or the fact that there is not one.
 *
 * A null cost means the model has no published price, which is not the same as
 * having been free. Printing $0.00 for it would be a lie about the bill.
 */
export function cost(value: number | null | undefined): string {
  return value === null || value === undefined ? "not priced" : money(value);
}

export function compactNumber(value: number | null | undefined): string {
  const amount = typeof value === "number" && Number.isFinite(value) ? value : 0;
  if (Math.abs(amount) < 1000) return String(amount);
  return new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 }).format(
    amount,
  );
}

export function plural(count: number, singular: string, pluralForm?: string): string {
  const word = count === 1 ? singular : (pluralForm ?? `${singular}s`);
  return `${count} ${word}`;
}

/** Turn an identifier such as "fetch_articles" into "Fetch articles". */
export function humanise(value: string | null | undefined): string {
  if (!value) return "";
  const spaced = value.replace(/[_-]+/g, " ").trim();
  if (!spaced) return "";
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

/** A readable one liner for a run's stats bag, whose keys are not fixed. */
export function describeStats(stats: Record<string, JsonValue> | null | undefined): string[] {
  if (!stats) return [];
  const parts: string[] = [];
  for (const [key, value] of Object.entries(stats)) {
    if (value === null || value === undefined) continue;
    if (typeof value === "object") continue;
    parts.push(`${humanise(key)}: ${String(value)}`);
  }
  return parts;
}

/** Turn a label into the kind of id the backend generates for it. */
export function slugify(value: string): string {
  return value
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 48);
}

export function hostOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

/** Clamp free text so a card stays the size it was designed to be. */
export function truncate(value: string, max: number): string {
  if (value.length <= max) return value;
  return `${value.slice(0, max - 1).trimEnd()}...`;
}
