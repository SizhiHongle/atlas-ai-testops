import { describe, expect, it } from "vitest";

import { mapSessionDto } from "./session-mapper";
import type { PlatformSessionDto } from "./session";

const SESSION_DTO: PlatformSessionDto = {
  authenticationMethod: "PASSWORD",
  expiresAt: "2026-07-20T08:00:00Z",
  project: {
    id: "11111111-1111-4111-8111-111111111111",
    tenantId: "22222222-2222-4222-8222-222222222222",
    projectKey: "CRM",
    name: "客户运营",
    status: "ACTIVE",
    revision: 1,
    createdAt: "2026-07-19T08:00:00Z",
    updatedAt: "2026-07-19T08:00:00Z"
  },
  roles: ["PROJECT_ADMIN", "RUN_OPERATOR"],
  tenant: {
    id: "22222222-2222-4222-8222-222222222222",
    slug: "atlas",
    name: "Atlas",
    status: "ACTIVE",
    revision: 1,
    createdAt: "2026-07-19T08:00:00Z",
    updatedAt: "2026-07-19T08:00:00Z"
  },
  user: {
    id: "33333333-3333-4333-8333-333333333333",
    displayName: "陈航",
    email: "chen.hang@example.com",
    status: "ACTIVE",
    revision: 1,
    createdAt: "2026-07-19T08:00:00Z",
    updatedAt: "2026-07-19T08:00:00Z"
  }
};

describe("mapSessionDto", () => {
  it("keeps backend DTO details outside the UI model", () => {
    const result = mapSessionDto(SESSION_DTO);

    expect(result.user).toEqual({
      id: SESSION_DTO.user.id,
      displayName: "陈航",
      email: "chen.hang@example.com",
      initials: "陈航"
    });
    expect(result.workspace).toEqual({
      tenantId: SESSION_DTO.tenant.id,
      tenantName: "Atlas",
      projectId: SESSION_DTO.project.id,
      projectName: "客户运营"
    });
    expect(result.roles).not.toBe(SESSION_DTO.roles);
    expect(result.expiresAt).toBeInstanceOf(Date);
  });
});
