import { describe, expect, it } from "vitest";

import {
  mapTaskPlanVersion,
  mapTaskSchedule,
  summarizeExecutionUnits
} from "./task-mapper";
import type {
  ExecutionUnitViewModel,
  TaskPlanVersionDto,
  TaskScheduleDto
} from "./task";

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

describe("task control catalog mapping", () => {
  it("preserves the exact frozen matrix axes", () => {
    const version = mapTaskPlanVersion({
      id: "version-1",
      taskPlanId: "plan-1",
      version: "1.2.0",
      versionRef: "task-plan/plan-1@1.2.0",
      pinnedCaseVersionIds: ["case-1", "case-2"],
      matrix: {
        environmentIds: ["env-1"],
        browserProfileVersionIds: ["browser-1", "browser-2"],
        identityProfileVersionIds: ["identity-1"],
        dataProfileVersionIds: ["data-1"]
      },
      policyDigests: {
        "infra-retry": `sha256:${"b".repeat(64)}`
      },
      contentDigest: `sha256:${"a".repeat(64)}`,
      publishedAt: "2026-07-19T08:00:00.000Z"
    } as unknown as TaskPlanVersionDto);

    expect(version).toMatchObject({
      pinnedCaseVersionIds: ["case-1", "case-2"],
      environmentIds: ["env-1"],
      browserProfileVersionIds: ["browser-1", "browser-2"],
      matrixSize: 4,
      caseCount: 2
    });
  });

  it("maps schedule fire times and the reusable frozen retry policy", () => {
    const schedule = mapTaskSchedule({
      id: "schedule-1",
      taskPlanVersionId: "version-1",
      name: "每日回归",
      scheduleKey: "daily.regression",
      status: "ACTIVE",
      syncStatus: "SYNCED",
      timeZoneName: "Asia/Shanghai",
      nextFireTimesUtc: ["2026-07-20T13:30:00.000Z"],
      retryPolicy: {
        schemaVersion: "atlas.task-retry-policy/0.1",
        infraRetryAttempts: 1,
        maxTotalInfraRetries: 8,
        initialBackoffSeconds: 2,
        maximumBackoffSeconds: 30,
        jitterPercent: 10,
        contentDigest: `sha256:${"c".repeat(64)}`
      },
      revision: 3
    } as unknown as TaskScheduleDto);

    expect(schedule.nextFireTimes[0]).toEqual(
      new Date("2026-07-20T13:30:00.000Z")
    );
    expect(schedule.retryPolicy.infraRetryAttempts).toBe(1);
  });
});
