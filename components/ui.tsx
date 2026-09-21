"use client";

import type {
  ButtonHTMLAttributes,
  InputHTMLAttributes,
  ReactNode,
  SelectHTMLAttributes,
  TextareaHTMLAttributes,
} from "react";
import { useId } from "react";

import {
  BUTTON_BASE,
  BUTTON_SIZES,
  BUTTON_VARIANTS,
  cx,
  type ButtonSize,
  type ButtonVariant,
} from "@/lib/classes";

export { cx };

/* -------------------------------------------------------------------------- */
/* Button                                                                      */
/* -------------------------------------------------------------------------- */

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  loading?: boolean;
}

export function Button({
  variant = "secondary",
  size = "md",
  loading = false,
  className,
  children,
  disabled,
  ...rest
}: ButtonProps) {
  return (
    <button
      type="button"
      {...rest}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={cx(BUTTON_BASE, BUTTON_VARIANTS[variant], BUTTON_SIZES[size], className)}
    >
      {loading ? <Spinner /> : null}
      {children}
    </button>
  );
}

export function Spinner({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 16 16"
      aria-hidden="true"
      className={cx("spin size-4 shrink-0", className)}
      fill="none"
    >
      <circle cx="8" cy="8" r="6.5" stroke="currentColor" strokeOpacity="0.25" strokeWidth="2" />
      <path d="M14.5 8A6.5 6.5 0 0 0 8 1.5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}

/* -------------------------------------------------------------------------- */
/* Surfaces                                                                    */
/* -------------------------------------------------------------------------- */

export function Card({
  children,
  className,
  as: Tag = "div",
  id,
}: {
  children: ReactNode;
  className?: string;
  as?: "div" | "section" | "article" | "li";
  /** Needed where something links to one card, such as a citation marker. */
  id?: string;
}) {
  return (
    <Tag
      id={id}
      className={cx(
        "rounded-xl border border-line bg-surface shadow-[var(--shadow-sm)]",
        className,
      )}
    >
      {children}
    </Tag>
  );
}

export function SectionHeading({
  title,
  description,
  action,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
      <div className="min-w-0">
        <h2 className="text-base font-semibold tracking-tight">{title}</h2>
        {description ? <p className="mt-1 text-sm text-muted">{description}</p> : null}
      </div>
      {action ? <div className="shrink-0">{action}</div> : null}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Badge                                                                       */
/* -------------------------------------------------------------------------- */

export type BadgeTone = "neutral" | "accent" | "ok" | "warn" | "danger";

const BADGE_TONES: Record<BadgeTone, string> = {
  neutral: "bg-raised text-muted border-line",
  accent: "bg-accent-soft text-accent border-transparent",
  ok: "bg-ok-soft text-ok border-transparent",
  warn: "bg-warn-soft text-warn border-transparent",
  danger: "bg-danger-soft text-danger border-transparent",
};

export function Badge({
  tone = "neutral",
  children,
  className,
  title,
}: {
  tone?: BadgeTone;
  children: ReactNode;
  className?: string;
  title?: string;
}) {
  return (
    <span
      title={title}
      className={cx(
        "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium whitespace-nowrap",
        BADGE_TONES[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}

/* -------------------------------------------------------------------------- */
/* Form controls                                                               */
/* -------------------------------------------------------------------------- */

const CONTROL =
  "w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink " +
  "placeholder:text-faint transition-colors hover:border-line-strong " +
  "focus:border-accent focus:outline-none focus-visible:outline-2 focus-visible:outline-[var(--ring)] " +
  "focus-visible:outline-offset-1 disabled:opacity-55";

export function Field({
  label,
  hint,
  error,
  children,
  htmlFor,
  className,
}: {
  label: string;
  hint?: string;
  error?: string;
  children: ReactNode;
  htmlFor?: string;
  className?: string;
}) {
  return (
    <div className={cx("flex flex-col gap-1.5", className)}>
      <label htmlFor={htmlFor} className="text-sm font-medium text-ink">
        {label}
      </label>
      {children}
      {error ? (
        <p className="text-xs text-danger">{error}</p>
      ) : hint ? (
        <p className="text-xs text-muted">{hint}</p>
      ) : null}
    </div>
  );
}

export interface TextInputProps extends InputHTMLAttributes<HTMLInputElement> {
  label?: string;
  hint?: string;
  error?: string;
}

export function TextInput({ label, hint, error, className, id, ...rest }: TextInputProps) {
  const generated = useId();
  const inputId = id ?? generated;
  const input = (
    <input
      id={inputId}
      {...rest}
      className={cx(CONTROL, error && "border-danger", className)}
      aria-invalid={error ? true : undefined}
    />
  );
  if (!label) return input;
  return (
    <Field label={label} hint={hint} error={error} htmlFor={inputId}>
      {input}
    </Field>
  );
}

export interface TextAreaProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  label?: string;
  hint?: string;
  error?: string;
}

export function TextArea({ label, hint, error, className, id, ...rest }: TextAreaProps) {
  const generated = useId();
  const inputId = id ?? generated;
  const field = (
    <textarea
      id={inputId}
      {...rest}
      className={cx(CONTROL, "resize-y", error && "border-danger", className)}
      aria-invalid={error ? true : undefined}
    />
  );
  if (!label) return field;
  return (
    <Field label={label} hint={hint} error={error} htmlFor={inputId}>
      {field}
    </Field>
  );
}

export interface SelectProps extends SelectHTMLAttributes<HTMLSelectElement> {
  label?: string;
  hint?: string;
}

export function Select({ label, hint, className, id, children, ...rest }: SelectProps) {
  const generated = useId();
  const inputId = id ?? generated;
  const field = (
    <select id={inputId} {...rest} className={cx(CONTROL, "pr-8", className)}>
      {children}
    </select>
  );
  if (!label) return field;
  return (
    <Field label={label} hint={hint} htmlFor={inputId}>
      {field}
    </Field>
  );
}

export function Toggle({
  checked,
  onChange,
  label,
  description,
  disabled,
}: {
  checked: boolean;
  onChange: (value: boolean) => void;
  label: string;
  description?: string;
  disabled?: boolean;
}) {
  return (
    <label
      className={cx(
        "flex cursor-pointer items-start gap-3",
        disabled && "cursor-not-allowed opacity-55",
      )}
    >
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
        className="mt-0.5 size-4 shrink-0 accent-[var(--accent)]"
      />
      <span className="min-w-0">
        <span className="block text-sm font-medium">{label}</span>
        {description ? <span className="block text-xs text-muted">{description}</span> : null}
      </span>
    </label>
  );
}

/* -------------------------------------------------------------------------- */
/* Segmented control                                                           */
/* -------------------------------------------------------------------------- */

export interface Segment<T extends string> {
  id: T;
  label: string;
  /** Shown on hover and to a screen reader, for an option whose label is short. */
  hint?: string;
}

/**
 * A row of mutually exclusive choices, for a handful of options that are worth
 * showing at once.
 *
 * It is buttons with aria-pressed rather than radios: nothing here is part of a
 * form, and each press takes effect immediately.
 */
export function SegmentedControl<T extends string>({
  options,
  value,
  onChange,
  label,
  className,
}: {
  options: ReadonlyArray<Segment<T>>;
  /** Null leaves every option unpressed, for a choice still being read back. */
  value: T | null;
  onChange: (next: T) => void;
  label: string;
  className?: string;
}) {
  return (
    <div
      role="group"
      aria-label={label}
      className={cx(
        "inline-flex w-fit rounded-lg border border-line bg-surface p-0.5",
        className,
      )}
    >
      {options.map((option) => (
        <button
          key={option.id}
          type="button"
          onClick={() => onChange(option.id)}
          aria-pressed={value === option.id}
          title={option.hint}
          className={cx(
            "rounded-md px-3 py-1.5 text-sm font-medium transition-colors",
            value === option.id ? "bg-accent text-accent-ink" : "text-muted hover:text-ink",
          )}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Feedback                                                                    */
/* -------------------------------------------------------------------------- */

export function Notice({
  tone = "neutral",
  title,
  children,
  action,
}: {
  tone?: BadgeTone;
  title: string;
  children?: ReactNode;
  action?: ReactNode;
}) {
  const tones: Record<BadgeTone, string> = {
    neutral: "border-line bg-raised",
    accent: "border-transparent bg-accent-soft",
    ok: "border-transparent bg-ok-soft",
    warn: "border-transparent bg-warn-soft",
    danger: "border-transparent bg-danger-soft",
  };
  const headings: Record<BadgeTone, string> = {
    neutral: "text-ink",
    accent: "text-accent",
    ok: "text-ok",
    warn: "text-warn",
    danger: "text-danger",
  };
  return (
    <div className={cx("rounded-lg border p-3 sm:p-4", tones[tone])}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className={cx("text-sm font-semibold break-anywhere", headings[tone])}>{title}</p>
          {children ? <div className="mt-1 text-sm text-ink/85 break-anywhere">{children}</div> : null}
        </div>
        {action ? <div className="shrink-0">{action}</div> : null}
      </div>
    </div>
  );
}
