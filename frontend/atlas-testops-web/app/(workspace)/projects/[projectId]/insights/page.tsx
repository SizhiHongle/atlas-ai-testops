import type { Metadata } from "next";

import { InsightPage } from "@/features/insight/ui/insight-page";

export const metadata: Metadata = {
  title: "洞察"
};

export default async function InsightsPage({
  params
}: Readonly<{ params: Promise<{ projectId: string }> }>) {
  const { projectId } = await params;
  return <InsightPage projectId={projectId} />;
}
