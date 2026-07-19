import { describe, expect, it } from "vitest";

import { mapIdentityWallet } from "./identity-mapper";
import type { IdentityWalletDto } from "./identity";

const BASE_TIME = "2026-07-19T08:00:00Z";

describe("mapIdentityWallet", () => {
  it("derives capacity and status from backend facts", () => {
    const dto: IdentityWalletDto = {
      environment: {
        id: "10000000-0000-4000-8000-000000000001",
        tenantId: "10000000-0000-4000-8000-000000000002",
        projectId: "10000000-0000-4000-8000-000000000003",
        environmentKey: "test",
        name: "测试环境",
        kind: "TEST",
        allowedOrigins: ["https://crm.example.com"],
        status: "ACTIVE",
        revision: 1,
        createdAt: BASE_TIME,
        updatedAt: BASE_TIME
      },
      entries: [
        {
          role: {
            id: "20000000-0000-4000-8000-000000000001",
            tenantId: "10000000-0000-4000-8000-000000000002",
            projectId: "10000000-0000-4000-8000-000000000003",
            roleKey: "sales",
            name: "销售身份",
            description: "销售角色",
            capabilities: ["customer.read"],
            status: "ACTIVE",
            revision: 1,
            createdAt: BASE_TIME,
            updatedAt: BASE_TIME
          },
          pool: {
            id: "30000000-0000-4000-8000-000000000001",
            tenantId: "10000000-0000-4000-8000-000000000002",
            projectId: "10000000-0000-4000-8000-000000000003",
            environmentId: "10000000-0000-4000-8000-000000000001",
            roleId: "20000000-0000-4000-8000-000000000001",
            poolKey: "sales-main",
            name: "销售主池",
            exclusive: true,
            defaultTtlSeconds: 900,
            cooldownSeconds: 60,
            healthFailureThreshold: 3,
            healthRetryCooldownSeconds: 300,
            status: "ACTIVE",
            revision: 1,
            createdAt: BASE_TIME,
            updatedAt: BASE_TIME
          },
          capacity: {
            poolId: "30000000-0000-4000-8000-000000000001",
            totalSlots: 14,
            availableSlots: 11,
            leasedSlots: 3,
            cooldownAccounts: 0,
            quarantinedAccounts: 0,
            unverifiedAccounts: 0
          },
          accounts: []
        }
      ]
    };

    const result = mapIdentityWallet(dto);

    expect(result.identities[0]).toMatchObject({
      roleKey: "sales",
      available: 11,
      leased: 3,
      total: 14,
      status: "READY",
      readyRatio: 11 / 14
    });
    expect(result.totals).toEqual({
      available: 11,
      leased: 3,
      quarantined: 0
    });
  });
});
