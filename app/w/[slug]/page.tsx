import { WorkspaceHome } from "./WorkspaceHome";

export default async function WorkspaceDigestPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  return <WorkspaceHome slug={slug} />;
}
