import type { components } from "@/shared/api/schema";

export type PlatformSessionDto = components["schemas"]["PlatformSessionView"];
export type PlatformRole = components["schemas"]["PlatformRole"];
export type LoginCommand = components["schemas"]["LoginCommand"];

export type SessionViewModel = {
  user: {
    id: string;
    displayName: string;
    email: string;
    initials: string;
  };
  workspace: {
    tenantId: string;
    tenantName: string;
    projectId: string;
    projectName: string;
  };
  roles: PlatformRole[];
  authenticationMethod: string;
  expiresAt: Date;
};
