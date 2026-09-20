"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { ThemeToggle } from "@/components/ThemeToggle";
import { useConfig } from "@/components/ConfigProvider";
import { cx } from "@/components/ui";

const LINKS = [
  { href: "/", label: "Workspaces" },
  { href: "/usage", label: "Usage" },
  { href: "/settings", label: "Settings" },
];

function isActive(pathname: string, href: string): boolean {
  if (href === "/") return pathname === "/" || pathname.startsWith("/w/");
  return pathname === href || pathname.startsWith(`${href}/`);
}

export function SiteHeader() {
  const pathname = usePathname() ?? "/";
  const { config } = useConfig();
  const appName = config?.app.name || "News Research";

  return (
    <header className="sticky top-0 z-30 border-b border-line bg-surface/85 backdrop-blur">
      <div className="mx-auto flex h-14 max-w-6xl items-center gap-3 px-3 sm:px-5">
        <Link
          href="/"
          className="flex min-w-0 items-center gap-2 rounded-md font-semibold tracking-tight"
        >
          <span
            aria-hidden="true"
            className="grid size-7 shrink-0 place-items-center rounded-md bg-accent text-accent-ink text-xs font-bold"
          >
            NR
          </span>
          <span className="truncate text-sm sm:text-base">{appName}</span>
        </Link>

        <nav aria-label="Primary" className="ml-auto flex items-center gap-0.5 sm:gap-1">
          {LINKS.map((link) => {
            const active = isActive(pathname, link.href);
            return (
              <Link
                key={link.href}
                href={link.href}
                aria-current={active ? "page" : undefined}
                className={cx(
                  "rounded-md px-2 py-1.5 text-[0.8125rem] font-medium transition-colors sm:px-3 sm:text-sm",
                  active ? "bg-raised text-ink" : "text-muted hover:bg-raised hover:text-ink",
                )}
              >
                {link.label}
              </Link>
            );
          })}
        </nav>

        <ThemeToggle />
      </div>
    </header>
  );
}
