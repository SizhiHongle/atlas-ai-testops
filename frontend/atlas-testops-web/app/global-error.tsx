"use client";

import { useEffect } from "react";

import { reportClientError } from "@/shared/observability/client";

export default function GlobalError({
  error,
  reset
}: Readonly<{ error: Error & { digest?: string }; reset: () => void }>) {
  useEffect(() => {
    reportClientError(error, {
      source: "route",
      operation: "global",
      digest: error.digest
    });
  }, [error]);

  return (
    <html lang="zh-CN">
      <body>
        <main style={{ padding: 40 }}>
          <h1>Atlas 前端无法继续运行</h1>
          <p>应用根边界捕获到异常，请重试或联系平台管理员。</p>
          <button type="button" onClick={reset}>
            重新加载
          </button>
        </main>
      </body>
    </html>
  );
}
