"use client";

import { usePathname, useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";

import { ErrorState } from "@/shared/ui/feedback/error-state";
import { LoadingState } from "@/shared/ui/feedback/loading-state";
import { WorkspaceShell } from "@/shared/ui/workspace-shell/workspace-shell";

import { useSessionQuery } from "../api/auth-queries";

type WorkspaceBoundaryProps = {
  expectedProjectId: string;
  children: ReactNode;
};

export function WorkspaceBoundary({
  expectedProjectId,
  children
}: Readonly<WorkspaceBoundaryProps>) {
  const pathname = usePathname();
  const router = useRouter();
  const session = useSessionQuery();

  useEffect(() => {
    if (session.data === null) {
      const next = encodeURIComponent(pathname);
      router.replace(`/login?next=${next}`);
      return;
    }
    if (
      session.data &&
      session.data.workspace.projectId !== expectedProjectId
    ) {
      const suffix = pathname.split("/").slice(3).join("/") || "space";
      router.replace(
        `/projects/${session.data.workspace.projectId}/${suffix}`
      );
    }
  }, [expectedProjectId, pathname, router, session.data]);

  if (session.isError) {
    return (
      <ErrorState
        detail={session.error.message}
        onRetry={() => void session.refetch()}
      />
    );
  }

  if (!session.data || session.data.workspace.projectId !== expectedProjectId) {
    return <LoadingState label="正在验证工作空间权限" />;
  }

  return <WorkspaceShell session={session.data}>{children}</WorkspaceShell>;
}
