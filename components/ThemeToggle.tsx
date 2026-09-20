"use client";

import { useCallback, useEffect, useState } from "react";

import { cx } from "@/components/ui";

export const THEME_STORAGE_KEY = "news-research.theme";

export type ThemeChoice = "system" | "light" | "dark";

/**
 * Applied before the first paint so the page never flashes the wrong theme.
 * Kept as a string because it has to run inline in the document head, before
 * React exists.
 */
export const THEME_BOOTSTRAP = `(function(){try{var t=localStorage.getItem(${JSON.stringify(
  THEME_STORAGE_KEY,
)});if(t==="light"||t==="dark"){document.documentElement.setAttribute("data-theme",t);}}catch(e){}})();`;

function readChoice(): ThemeChoice {
  try {
    const stored = window.localStorage.getItem(THEME_STORAGE_KEY);
    if (stored === "light" || stored === "dark") return stored;
  } catch {
    // Storage can be blocked. The operating system preference still applies.
  }
  return "system";
}

function apply(choice: ThemeChoice): void {
  const root = document.documentElement;
  if (choice === "system") {
    root.removeAttribute("data-theme");
  } else {
    root.setAttribute("data-theme", choice);
  }
  try {
    if (choice === "system") {
      window.localStorage.removeItem(THEME_STORAGE_KEY);
    } else {
      window.localStorage.setItem(THEME_STORAGE_KEY, choice);
    }
  } catch {
    // The choice still holds for this page view.
  }
}

const OPTIONS: Array<{ value: ThemeChoice; label: string; short: string }> = [
  { value: "light", label: "Light", short: "Lt" },
  { value: "system", label: "System", short: "Sys" },
  { value: "dark", label: "Dark", short: "Dk" },
];

export function ThemeToggle() {
  const [choice, setChoice] = useState<ThemeChoice>("system");
  const [ready, setReady] = useState(false);

  useEffect(() => {
    setChoice(readChoice());
    setReady(true);
  }, []);

  const pick = useCallback((next: ThemeChoice) => {
    setChoice(next);
    apply(next);
  }, []);

  return (
    <div
      role="radiogroup"
      aria-label="Colour theme"
      className="inline-flex items-center rounded-lg border border-line bg-surface p-0.5"
    >
      {OPTIONS.map((option) => {
        const selected = ready && choice === option.value;
        return (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={selected}
            onClick={() => pick(option.value)}
            title={`${option.label} theme`}
            className={cx(
              "rounded-md px-2 py-1 text-xs font-medium transition-colors",
              selected ? "bg-accent-soft text-accent" : "text-muted hover:text-ink",
            )}
          >
            <span aria-hidden="true" className="sm:hidden">
              {option.short}
            </span>
            <span className="hidden sm:inline">{option.label}</span>
            <span className="sr-only">{option.label} theme</span>
          </button>
        );
      })}
    </div>
  );
}
