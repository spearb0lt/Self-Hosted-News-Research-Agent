/**
 * Class name helpers.
 *
 * These live outside the component modules because server components need them
 * too, and a function exported from a "use client" module cannot be called on
 * the server.
 */

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";
export type ButtonSize = "sm" | "md";

/** Join class names, dropping anything falsy. */
export function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}

export const BUTTON_BASE =
  "inline-flex items-center justify-center gap-2 rounded-lg border font-medium " +
  "transition-colors disabled:cursor-not-allowed disabled:opacity-55";

export const BUTTON_VARIANTS: Record<ButtonVariant, string> = {
  primary:
    "border-transparent bg-accent text-accent-ink hover:bg-accent-hover shadow-[var(--shadow-sm)]",
  secondary: "border-line bg-surface text-ink hover:bg-raised",
  ghost: "border-transparent bg-transparent text-muted hover:bg-raised hover:text-ink",
  danger: "border-transparent bg-danger-soft text-danger hover:brightness-95",
};

export const BUTTON_SIZES: Record<ButtonSize, string> = {
  sm: "h-8 px-3 text-[0.8125rem]",
  md: "h-10 px-4 text-sm",
};

/** The same shape as Button, for a link that navigates rather than acts. */
export function linkButtonClass(
  variant: ButtonVariant = "secondary",
  size: ButtonSize = "md",
  className?: string,
): string {
  return cx(BUTTON_BASE, BUTTON_VARIANTS[variant], BUTTON_SIZES[size], "no-underline", className);
}
