import { describe, expect, it } from "vitest";

import { mapFixtureCatalog } from "./fixture-mapper";

const TIME = "2026-07-19T08:00:00Z";

describe("mapFixtureCatalog", () => {
  it("maps asset DTOs without inventing health percentages", () => {
    const result = mapFixtureCatalog({
      atoms: [
        {
          id: "10000000-0000-4000-8000-000000000001",
          tenantId: "10000000-0000-4000-8000-000000000002",
          projectId: "10000000-0000-4000-8000-000000000003",
          atomKey: "customer.create",
          name: "创建客户",
          businessDomain: "customer",
          description: "创建客户事实",
          status: "ACTIVE",
          revision: 1,
          createdAt: TIME,
          updatedAt: TIME,
          latestVersion: "2.4",
          latestVersionStatus: "PUBLISHED",
          latestEffect: "CREATE",
          cleanupCapable: true,
          inputPorts: ["ownerId"],
          outputPorts: ["customerId"]
        }
      ],
      blueprints: []
    });

    expect(result.atoms[0]).toEqual({
      id: "10000000-0000-4000-8000-000000000001",
      key: "customer.create",
      name: "创建客户",
      domain: "customer",
      description: "创建客户事实",
      version: "2.4",
      versionState: "PUBLISHED",
      definitionStatus: "ACTIVE",
      effect: "CREATE",
      cleanupCapable: true,
      inputPorts: ["ownerId"],
      outputPorts: ["customerId"]
    });
    expect(result.atoms[0]).not.toHaveProperty("health");
  });
});
