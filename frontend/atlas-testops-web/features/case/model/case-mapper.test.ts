import { describe, expect, it } from "vitest";

import { mapCaseWorkspace } from "./case-mapper";
import type {
  CaseVersionDto,
  DebugRunDto,
  WorkflowDraftSnapshotDto
} from "./case";

const TIME = "2026-07-19T08:00:00Z";

describe("mapCaseWorkspace", () => {
  it("uses authoritative graph layout and validation", () => {
    const draft = {
      id: "10000000-0000-4000-8000-000000000001",
      tenantId: "10000000-0000-4000-8000-000000000002",
      projectId: "10000000-0000-4000-8000-000000000003",
      testCaseId: "10000000-0000-4000-8000-000000000004",
      intentVersionRef: "atlas.test-intent/0.1:1",
      semanticRevision: 7,
      layoutRevision: 4,
      semanticDigest: "digest",
      graph: {
        schemaVersion: "atlas.workflow-graph/0.1",
        nodes: [
          {
            id: "node-a",
            kind: "fixture.create",
            phase: "setup",
            versionRef: "atom:1.0.0",
            terminal: false,
            inputPorts: [],
            outputPorts: []
          }
        ],
        edges: []
      },
      layout: { "node-a": { x: 44, y: 55 } },
      validation: {
        valid: true,
        issues: [],
        executionLevels: [["node-a"]],
        matchedRequiredInputs: 0,
        totalRequiredInputs: 0
      },
      updatedBy: "human",
      createdAt: TIME,
      updatedAt: TIME
    } satisfies WorkflowDraftSnapshotDto;

    const result = mapCaseWorkspace(
      draft,
      [] as DebugRunDto[],
      [] as CaseVersionDto[]
    );

    expect(result.draft.valid).toBe(true);
    expect(result.draft.nodes[0]).toMatchObject({
      id: "node-a",
      x: 44,
      y: 55,
      phase: "setup"
    });
    expect(result.draft.executionLevels).toEqual([["node-a"]]);
  });
});
