import type { Metadata, Viewport } from "next";

import "./globals.css";
import { ConfigProvider } from "@/components/ConfigProvider";
import { SiteHeader } from "@/components/SiteHeader";
import { THEME_BOOTSTRAP } from "@/components/ThemeToggle";

// The header reads the name from the API, which reads APP_NAME. The tab title
// is rendered before that call returns, so it reads the same variable directly
// rather than showing a second, different name for the same deployment.
const APP_NAME = process.env.APP_NAME || "Newsroom";

export const metadata: Metadata = {
  title: {
    default: APP_NAME,
    template: `%s | ${APP_NAME}`,
  },
  description:
    "Collect the news you care about, group it into events, and read a short daily digest you can ask questions of.",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f6f7f9" },
    { media: "(prefers-color-scheme: dark)", color: "#0c0f15" },
  ],
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        {/* Runs before the first paint so a chosen theme never flashes. */}
        <script dangerouslySetInnerHTML={{ __html: THEME_BOOTSTRAP }} />
      </head>
      {/* Extensions inject attributes into body before React hydrates.
          ColorZilla adds cz-shortcut-listen, Grammarly adds its own. The
          warning is about this element only, not its children. */}
      <body className="min-h-dvh antialiased" suppressHydrationWarning>
        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-50 focus:rounded-lg focus:border focus:border-line focus:bg-surface focus:px-3 focus:py-2 focus:text-sm"
        >
          Skip to content
        </a>
        <ConfigProvider>
          <SiteHeader />
          <main id="main" className="mx-auto w-full max-w-6xl px-3 py-6 sm:px-5 sm:py-8">
            {children}
          </main>
          <footer className="mx-auto w-full max-w-6xl px-3 pb-10 text-xs text-faint sm:px-5">
            API keys you paste are stored in this browser only and are sent with your own
            requests. Nothing is shared with anyone else.
          </footer>
        </ConfigProvider>
      </body>
    </html>
  );
}
