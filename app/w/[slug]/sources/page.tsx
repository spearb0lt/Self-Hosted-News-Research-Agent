import { SourcesView } from "./SourcesView";

export default async function SourcesPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  return <SourcesView slug={slug} />;
}
