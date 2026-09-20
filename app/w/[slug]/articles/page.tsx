import { ArticlesView } from "./ArticlesView";

export default async function ArticlesPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  return <ArticlesView slug={slug} />;
}
