import { describe, expect, it } from "vitest";

import { mapUnitAttempt } from "./live-mapper";
import type { UnitAttemptDto } from "./live";

describe("mapUnitAttempt", () => {
  it("keeps lifecycle, quality and hygiene as independent facts", () => {
    const attempt = mapUnitAttempt({
      id: "00000000-0000-0000-0000-000000000001",
      tenantId: "00000000-0000-0000-0000-000000000002",
      projectId: "00000000-0000-0000-0000-000000000003",
      taskRunId: "00000000-0000-0000-0000-000000000004",
      executionUnitId: "00000000-0000-0000-0000-000000000005",
      manifestHash: `sha256:${"1".repeat(64)}`,
      unitKey: `sha256:${"2".repeat(64)}`,
      caseVersionId: "00000000-0000-0000-0000-000000000006",
      attemptNumber: 2,
      lifecycle: "CLOSED",
      quality: "FAILED",
      hygiene: "CLEANED",
      queuedAt: "2026-07-19T00:00:00Z",
      executionDeadline: "2026-07-19T00:15:00Z",
      revision: 4,
      createdAt: "2026-07-19T00:00:00Z",
      updatedAt: "2026-07-19T00:10:00Z"
    } satisfies UnitAttemptDto);

    expect(attempt).toMatchObject({
      attemptNumber: 2,
      lifecycle: "CLOSED",
      quality: "FAILED",
      hygiene: "CLEANED"
    });
  });
});
