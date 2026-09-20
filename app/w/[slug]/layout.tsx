import type { ReactNode } from "react";

import { WorkspaceChrome } from "@/components/WorkspaceChrome";

export default async function WorkspaceLayout({
  children,
  params,
}: {
  children: ReactNode;
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  return (
    <div>
      <WorkspaceChrome slug={slug} />
      {children}
    </div>
  );
}
