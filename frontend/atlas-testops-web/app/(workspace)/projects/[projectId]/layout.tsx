import type { ReactNode } from "react";

import { WorkspaceBoundary } from "@/features/auth/ui/workspace-boundary";

type WorkspaceLayoutProps = {
  children: ReactNode;
  params: Promise<{ projectId: string }>;
};

export default async function WorkspaceLayout({
  children,
  params
}: Readonly<WorkspaceLayoutProps>) {
  const { projectId } = await params;
  return (
    <WorkspaceBoundary expectedProjectId={projectId}>
      {children}
    </WorkspaceBoundary>
  );
}
