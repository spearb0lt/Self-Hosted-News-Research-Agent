import { DigestView } from "./DigestView";

export default async function WorkspaceDigestPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  return <DigestView slug={slug} />;
}
