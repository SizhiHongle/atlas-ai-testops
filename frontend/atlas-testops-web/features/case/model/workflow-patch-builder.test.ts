import { describe, expect, it } from "vitest";

import {
  createHumanWorkflowPatch,
  decodePortReference,
  encodePortReference,
  parsePortDefinitions
} from "./workflow-patch-builder";

describe("workflow patch builder", () => {
  it("parses explicit typed ports without inventing defaults in the UI", () => {
    expect(
      parsePortDefinitions(
        "customer_id | crm.customer.id | data | required\nready | control.ready | control | optional"
      )
    ).toEqual([
      {
        key: "customer_id",
        semanticType: "crm.customer.id",
        kind: "data",
        required: true,
        sensitive: false
      },
      {
        key: "ready",
        semanticType: "control.ready",
        kind: "control",
        required: false,
        sensitive: false
      }
    ]);
  });

  it("round-trips a port reference and creates a human revision patch", () => {
    const encoded = encodePortReference("node-a", {
      key: "customer_id",
      semanticType: "crm.customer.id",
      kind: "data",
      required: true,
      sensitive: false
    });
    expect(decodePortReference(encoded)).toEqual({
      nodeId: "node-a",
      key: "customer_id",
      semanticType: "crm.customer.id",
      kind: "data"
    });

    const patch = createHumanWorkflowPatch(
      7,
      { op: "REMOVE_NODE", nodeId: "node-a" },
      "Remove obsolete setup node."
    );
    expect(patch).toMatchObject({
      baseSemanticRevision: 7,
      source: "human",
      rationaleSummary: "Remove obsolete setup node.",
      operations: [{ op: "REMOVE_NODE", nodeId: "node-a" }]
    });
    expect(patch.patchId).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
    );
  });

  it("rejects malformed port definitions before preview", () => {
    expect(() =>
      parsePortDefinitions("1bad | crm.customer.id | data | required")
    ).toThrow("Port Key");
  });
});
