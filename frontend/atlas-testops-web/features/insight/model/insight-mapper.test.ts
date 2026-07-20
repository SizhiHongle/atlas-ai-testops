import { describe, expect, it } from "vitest";

import {
  mapInsightBrief,
  mapInsightSnapshot
} from "./insight-mapper";
import type {
  InsightBriefDto,
  InsightSnapshotDto
} from "./insight";

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

    const mapped = mapInsightBrief(dto);

    expect(mapped.current.trustedPassRate).toMatchObject({
      percentage: null,
      sampleStatus: "NO_DATA"
    });
    expect(mapped).toMatchObject({
      mode: "LIVE",
      snapshot: null,
      metricPolicyVersion: "0.1.0"
    });
  });

  it("maps immutable snapshot identity separately from the DatasetCut", () => {
    const brief = {
      tenantId: "00000000-0000-0000-0000-000000000001",
      projectId: "00000000-0000-0000-0000-000000000002",
      windowDays: 30,
      metricPolicyVersion: "0.1.0",
      schemaVersion: "atlas.insight-snapshot/0.1",
      metricDefinitions: [],
      current: {
        startAt: "2026-06-19T00:00:00Z",
        endAt: "2026-07-19T00:00:00Z",
        taskRunCount: 1,
        executionUnitCount: 30,
        trustedPassRate: {
          metricKey: "quality.trusted_pass_rate",
          numerator: 29,
          denominator: 30,
          basisPoints: 9667,
          sampleStatus: "ENOUGH",
          metricVersion: "1.0.0"
        },
        autonomousTrustedPassRate: {
          metricKey: "quality.autonomous_trusted_pass_rate",
          numerator: 28,
          denominator: 30,
          basisPoints: 9333,
          sampleStatus: "ENOUGH",
          metricVersion: "1.0.0"
        },
        methodHealthRate: {
          metricKey: "quality.method_health_rate",
          numerator: 30,
          denominator: 30,
          basisPoints: 10000,
          sampleStatus: "ENOUGH",
          metricVersion: "1.0.0"
        }
      },
      baseline: {
        startAt: "2026-05-20T00:00:00Z",
        endAt: "2026-06-19T00:00:00Z",
        taskRunCount: 0,
        executionUnitCount: 0,
        trustedPassRate: {
          metricKey: "quality.trusted_pass_rate",
          numerator: 0,
          denominator: 0,
          basisPoints: null,
          sampleStatus: "NO_DATA",
          metricVersion: "1.0.0"
        },
        autonomousTrustedPassRate: {
          metricKey: "quality.autonomous_trusted_pass_rate",
          numerator: 0,
          denominator: 0,
          basisPoints: null,
          sampleStatus: "NO_DATA",
          metricVersion: "1.0.0"
        },
        methodHealthRate: {
          metricKey: "quality.method_health_rate",
          numerator: 0,
          denominator: 0,
          basisPoints: null,
          sampleStatus: "NO_DATA",
          metricVersion: "1.0.0"
        }
      },
      deltas: {
        trustedPassRate: null,
        autonomousTrustedPassRate: null,
        methodHealthRate: null
      },
      terrain: [],
      datasetCut: {
        schemaVersion: "atlas.insight-dataset-cut/0.1",
        asOf: "2026-07-19T00:00:00Z",
        sourceSnapshotIds: [
          "00000000-0000-0000-0000-000000000004"
        ],
        sourceSnapshotHashes: [`sha256:${"1".repeat(64)}`],
        gateDecisionIds: [],
        gateDecisionHashes: [],
        sourceSetDigest: `sha256:${"2".repeat(64)}`,
        queryHash: `sha256:${"3".repeat(64)}`,
        authScopeHash: `sha256:${"4".repeat(64)}`
      },
      generatedAt: "2026-07-19T00:00:00Z",
      id: "00000000-0000-0000-0000-000000000005",
      clientMutationId: "pin-test",
      createdAt: "2026-07-19T00:00:01Z",
      createdBy: "00000000-0000-0000-0000-000000000003",
      requestHash: `sha256:${"5".repeat(64)}`,
      snapshotHash: `sha256:${"6".repeat(64)}`
    } satisfies InsightSnapshotDto;

    const mapped = mapInsightSnapshot(brief);

    expect(mapped).toMatchObject({
      mode: "PINNED",
      snapshot: {
        id: brief.id,
        snapshotHash: brief.snapshotHash
      },
      datasetCut: {
        sourceSnapshotCount: 1,
        authScopeHash: brief.datasetCut.authScopeHash
      }
    });
  });
});
