import type { Metadata } from "next";

import { CasePage } from "@/features/case/ui/case-page";

export const metadata: Metadata = {
  title: "用例"
};

export default async function CasesPage({
  params
}: Readonly<{ params: Promise<{ projectId: string }> }>) {
  const { projectId } = await params;
  return <CasePage projectId={projectId} />;
}
