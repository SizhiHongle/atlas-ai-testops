import type { Metadata } from "next";

import { IdentityPage } from "@/features/identity/ui/identity-page";

export const metadata: Metadata = {
  title: "身份"
};

export default async function IdentitiesPage({
  params
}: Readonly<{ params: Promise<{ projectId: string }> }>) {
  const { projectId } = await params;
  return <IdentityPage projectId={projectId} />;
}
