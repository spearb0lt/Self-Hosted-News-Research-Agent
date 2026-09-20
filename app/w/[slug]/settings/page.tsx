import { WorkspaceSettingsView } from "./WorkspaceSettingsView";

export default async function WorkspaceSettingsPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  return <WorkspaceSettingsView slug={slug} />;
}
