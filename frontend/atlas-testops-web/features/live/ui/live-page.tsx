"use client";

import { useSearchParams } from "next/navigation";

import { BatchLiveConsole } from "./batch-live-console";
import { DebugLiveTheatre } from "./debug-live-theatre";

export function LivePage({ projectId }: Readonly<{ projectId: string }>) {
  const searchParams = useSearchParams();
  const debugRunId = searchParams.get("debugRunId");

  if (debugRunId) {
    return (
      <DebugLiveTheatre
        debugRunId={debugRunId}
        projectId={projectId}
        requestedCaseId={searchParams.get("caseId")}
      />
    );
  }

  return <BatchLiveConsole projectId={projectId} />;
}
