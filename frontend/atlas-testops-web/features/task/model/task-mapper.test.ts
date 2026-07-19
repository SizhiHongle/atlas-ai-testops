import { describe, expect, it } from "vitest";

import { summarizeExecutionUnits } from "./task-mapper";
import type { ExecutionUnitViewModel } from "./task";

function unit(
  lifecycle: string,
  quality: string,
  ordinal: number
): ExecutionUnitViewModel {
  return {
    id: `unit-${ordinal}`,
    ordinal,
    caseVersionId: `case-${ordinal}`,
    environmentId: "environment",
    browserProfileVersionId: "browser",
    lifecycle,
    quality,
    hygiene: "CLEANED"
  };
}

describe("summarizeExecutionUnits", () => {
  it("derives progress and quality counts only from real unit axes", () => {
    const result = summarizeExecutionUnits([
      unit("CLOSED", "PASSED", 1),
      unit("CLOSED", "FAILED", 2),
      unit("RUNNING", "PENDING", 3),
      unit("QUEUED", "PENDING", 4)
    ]);

    expect(result).toMatchObject({
      total: 4,
      closed: 2,
      running: 1,
      queued: 1,
      passed: 1,
      failed: 1,
      progress: 50
    });
  });
});
