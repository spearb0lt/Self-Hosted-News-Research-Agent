import { ChatView } from "./ChatView";

export default async function ChatPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  return <ChatView slug={slug} />;
}
