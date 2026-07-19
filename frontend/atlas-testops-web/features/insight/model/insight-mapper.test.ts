import { describe, expect, it } from "vitest";

import { mapInsightBrief } from "./insight-mapper";
import type { InsightBriefDto } from "./insight";

describe("mapInsightBrief", () => {
  it("keeps NO_DATA distinct from a zero metric", () => {
    const metric = {
      metricKey: "quality.trusted_pass_rate" as const,
      numerator: 0,
      denominator: 0,
      basisPoints: null,
      sampleStatus: "NO_DATA" as const,
      metricVersion: "1.0.0" as const
    };
    const window = {
      startAt: "2026-06-19T00:00:00Z",
      endAt: "2026-07-19T00:00:00Z",
      taskRunCount: 0,
      executionUnitCount: 0,
      trustedPassRate: metric,
      autonomousTrustedPassRate: {
        ...metric,
        metricKey: "quality.autonomous_trusted_pass_rate" as const
      },
      methodHealthRate: {
        ...metric,
        metricKey: "quality.method_health_rate" as const
      }
    };
    const dto = {
      tenantId: "00000000-0000-0000-0000-000000000001",
      projectId: "00000000-0000-0000-0000-000000000002",
      windowDays: 30,
      metricPolicyVersion: "0.1.0",
      schemaVersion: "atlas.insight-brief/0.1",
      metricDefinitions: [],
      current: window,
      baseline: window,
      deltas: {
        trustedPassRate: null,
        autonomousTrustedPassRate: null,
        methodHealthRate: null
      },
      terrain: [],
      datasetCut: {
        schemaVersion: "atlas.insight-dataset-cut/0.1",
        asOf: "2026-07-19T00:00:00Z",
        sourceSnapshotIds: [],
        sourceSnapshotHashes: [],
        gateDecisionIds: [],
        gateDecisionHashes: [],
        sourceSetDigest: `sha256:${"1".repeat(64)}`,
        queryHash: `sha256:${"2".repeat(64)}`,
        authScopeHash: `sha256:${"3".repeat(64)}`
      },
      generatedAt: "2026-07-19T00:00:00Z"
    } satisfies InsightBriefDto;

    expect(mapInsightBrief(dto).current.trustedPassRate).toMatchObject({
      percentage: null,
      sampleStatus: "NO_DATA"
    });
  });
});
