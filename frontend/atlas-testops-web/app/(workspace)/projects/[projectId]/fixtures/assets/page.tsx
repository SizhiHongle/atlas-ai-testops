import type { Metadata } from "next";

import { AssetsPage } from "@/features/fixture/ui/assets-page";

export const metadata: Metadata = {
  title: "资产"
};

export default async function AssetsRoute({
  params
}: Readonly<{ params: Promise<{ projectId: string }> }>) {
  const { projectId } = await params;
  return <AssetsPage projectId={projectId} />;
}
