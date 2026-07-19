"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { ErrorState } from "@/shared/ui/feedback/error-state";
import { LoadingState } from "@/shared/ui/feedback/loading-state";

import { useSessionQuery } from "../api/auth-queries";

export function EntryRoute() {
  const router = useRouter();
  const session = useSessionQuery();

  useEffect(() => {
    if (session.data === null) {
      router.replace("/login");
    } else if (session.data) {
      router.replace(`/projects/${session.data.workspace.projectId}/space`);
    }
  }, [router, session.data]);

  if (session.isError) {
    return (
      <ErrorState
        detail={session.error.message}
        onRetry={() => void session.refetch()}
      />
    );
  }

  return <LoadingState label="正在定位你的测试空间" />;
}
