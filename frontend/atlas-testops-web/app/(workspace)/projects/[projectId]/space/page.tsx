import type { Metadata } from "next";

import { SpaceDashboard } from "@/features/space/ui/space-dashboard";

export const metadata: Metadata = {
  title: "测试空间"
};

export default async function SpacePage({
  params
}: Readonly<{ params: Promise<{ projectId: string }> }>) {
  const { projectId } = await params;
  return <SpaceDashboard projectId={projectId} />;
}
