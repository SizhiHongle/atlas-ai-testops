"use client";

import { useEffect } from "react";

import { reportClientError } from "@/shared/observability/client";
import { ErrorState } from "@/shared/ui/feedback/error-state";

export default function RootError({
  error,
  reset
}: Readonly<{ error: Error & { digest?: string }; reset: () => void }>) {
  useEffect(() => {
    reportClientError(error, {
      source: "route",
      operation: "root",
      digest: error.digest
    });
  }, [error]);

  return (
    <ErrorState
      detail="页面运行时出现异常。错误已明确中止渲染，没有使用演示数据替代。"
      onRetry={reset}
    />
  );
}
