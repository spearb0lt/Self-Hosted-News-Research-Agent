"use client";

import { useEffect } from "react";

import { Button } from "@/components/ui";

/** The last resort when a page throws. Nothing here touches stored keys. */
export default function AppError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    // Only the message, so nothing a request carried can end up in a log.
    console.error(error.message);
  }, [error]);

  return (
    <div className="mx-auto max-w-md py-16 text-center" role="alert">
      <h1 className="text-2xl font-semibold tracking-tight">This page stopped working</h1>
      <p className="mt-2 text-sm text-muted break-anywhere">{error.message}</p>
      <div className="mt-6 flex justify-center gap-2">
        <Button variant="primary" onClick={reset}>
          Try again
        </Button>
        <Button variant="secondary" onClick={() => window.location.reload()}>
          Reload the page
        </Button>
      </div>
    </div>
  );
}
