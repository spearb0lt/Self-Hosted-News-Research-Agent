import type { Metadata } from "next";

import { ResearchView } from "./ResearchView";

export const metadata: Metadata = {
  title: "Research",
  description: "Search the news for one topic across every available backend.",
};

export default function ResearchPage() {
  return <ResearchView />;
}
