"use client";

import type { ReactNode } from "react";

import type { PlatformRole } from "../model/session";
import { useSessionQuery } from "../api/auth-queries";
import { AccessDenied } from "@/shared/ui/feedback/access-denied";
import { ErrorState } from "@/shared/ui/feedback/error-state";
import { LoadingState } from "@/shared/ui/feedback/loading-state";

type PermissionGuardProps = {
  anyOf: readonly PlatformRole[];
  children: ReactNode;
};

export function PermissionGuard({
  anyOf,
  children
}: Readonly<PermissionGuardProps>) {
  const session = useSessionQuery();

  if (session.isPending) {
    return <LoadingState label="正在验证操作权限" />;
  }
  if (session.isError) {
    return (
      <ErrorState
        detail={session.error.message}
        onRetry={() => void session.refetch()}
      />
    );
  }

  const allowed = session.data?.roles.some((role) => anyOf.includes(role));

  if (!allowed) {
    return <AccessDenied />;
  }
  return children;
}
