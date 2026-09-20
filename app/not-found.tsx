import Link from "next/link";

import { linkButtonClass } from "@/lib/classes";

export default function NotFound() {
  return (
    <div className="mx-auto max-w-md py-16 text-center">
      <h1 className="text-2xl font-semibold tracking-tight">Nothing here</h1>
      <p className="mt-2 text-sm text-muted">
        That page does not exist. It may have been a workspace that was deleted.
      </p>
      <div className="mt-6">
        <Link href="/" className={linkButtonClass("primary")}>
          Back to workspaces
        </Link>
      </div>
    </div>
  );
}
