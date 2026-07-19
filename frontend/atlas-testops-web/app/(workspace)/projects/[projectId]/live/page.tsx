import type { Metadata } from "next";

import { LivePage } from "@/features/live/ui/live-page";

export const metadata: Metadata = {
  title: "现场"
};

export default async function LiveRoute({
  params
}: Readonly<{ params: Promise<{ projectId: string }> }>) {
  const { projectId } = await params;
  return <LivePage projectId={projectId} />;
}
