import type { Metadata } from "next";

import { ResultPage } from "@/features/result/ui/result-page";

export const metadata: Metadata = {
  title: "结果"
};

export default async function ResultsPage({
  params
}: Readonly<{ params: Promise<{ projectId: string }> }>) {
  const { projectId } = await params;
  return <ResultPage projectId={projectId} />;
}
