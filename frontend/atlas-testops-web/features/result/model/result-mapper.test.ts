import { describe, expect, it } from "vitest";

import { mapTaskResult } from "./result-mapper";
import type { TaskResultViewDto } from "./result";

describe("mapTaskResult", () => {
  it("keeps exact pass-rate fractions and derives only display percentage", () => {
    const axes = {
      outcomeClass: {
        business: 2,
        dependency: 0,
        platform: 0,
        user: 0,
        automation: 0,
        policy: 0,
        unknown: 0
      },
      executionInfluence: {
        autonomous: 2,
        manualAssisted: 0,
        manualOnly: 0
      },
      stability: {
        unknown: 0,
        stable: 2,
        infraRecovered: 0,
        flakySuspect: 0,
        flakyConfirmed: 0
      },
      evidenceCompleteness: {
        pending: 0,
        complete: 2,
        partial: 0,
        missing: 0,
        notApplicable: 0
      },
      evidenceIntegrity: { unverified: 0, verified: 2, invalid: 0 },
      dataHygiene: {
        pending: 0,
        cleaned: 2,
        cleanupFailed: 0,
        leaked: 0,
        notApplicable: 0
      }
    };
    const dto = {
      taskRunId: "00000000-0000-0000-0000-000000000001",
      selection: "LATEST",
      resultSnapshot: {
        id: "00000000-0000-0000-0000-000000000002",
        tenantId: "00000000-0000-0000-0000-000000000003",
        projectId: "00000000-0000-0000-0000-000000000004",
        taskRunId: "00000000-0000-0000-0000-000000000001",
        manifestHash: `sha256:${"1".repeat(64)}`,
        revision: 1,
        unitResolutionRevisionIds: [
          "00000000-0000-0000-0000-000000000005"
        ],
        inputResolutionSetHash: `sha256:${"2".repeat(64)}`,
        aggregationPolicyDigest: `sha256:${"3".repeat(64)}`,
        aggregationPolicyVersion: "0.1.0",
        finality: "QUALITY_FINAL",
        schemaVersion: "atlas.task-result-snapshot/0.1",
        projectionWatermark: "2026-07-19T00:00:00Z",
        manifestCount: 2,
        verdictCounts: {
          passed: 1,
          failed: 1,
          inconclusive: 0,
          notEvaluated: 0
        },
        axisDistributions: axes,
        rawPassRate: { numerator: 1, denominator: 2 },
        trustedPassRate: { numerator: 1, denominator: 2 },
        autonomousPassRate: { numerator: 1, denominator: 2 },
        decisivePassRate: { numerator: 1, denominator: 2 },
        createdAt: "2026-07-19T00:00:00Z",
        snapshotHash: `sha256:${"4".repeat(64)}`
      },
      projectionWatermark: "2026-07-19T00:00:00Z"
    } satisfies TaskResultViewDto;

    expect(mapTaskResult(dto).snapshot.rawPassRate).toEqual({
      numerator: 1,
      denominator: 2,
      percentage: 50
    });
  });
});
