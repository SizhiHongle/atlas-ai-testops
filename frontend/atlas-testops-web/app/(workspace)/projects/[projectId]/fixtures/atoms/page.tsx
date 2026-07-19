import type { Metadata } from "next";

import { AtomsPage } from "@/features/fixture/ui/atoms-page";

export const metadata: Metadata = {
  title: "原子"
};

export default async function AtomsRoute({
  params
}: Readonly<{ params: Promise<{ projectId: string }> }>) {
  const { projectId } = await params;
  return <AtomsPage projectId={projectId} />;
}
