export const ATLAS_API_BASE_URL = "/api/atlas";

export type LoginWorkspace = {
  id: "primary" | "staging";
  label: string;
  tenantId: string;
  projectId: string;
  configured: boolean;
};

const primaryWorkspace: LoginWorkspace = {
  id: "primary",
  label:
    process.env.NEXT_PUBLIC_ATLAS_WORKSPACE_LABEL ??
    "客户运营 · CRM R26.07",
  tenantId: process.env.NEXT_PUBLIC_ATLAS_TENANT_ID ?? "",
  projectId: process.env.NEXT_PUBLIC_ATLAS_PROJECT_ID ?? "",
  configured: Boolean(
    process.env.NEXT_PUBLIC_ATLAS_TENANT_ID &&
      process.env.NEXT_PUBLIC_ATLAS_PROJECT_ID
  )
};

const stagingWorkspace: LoginWorkspace = {
  id: "staging",
  label:
    process.env.NEXT_PUBLIC_ATLAS_STAGING_WORKSPACE_LABEL ??
    "预发验证 · STAGING",
  tenantId: process.env.NEXT_PUBLIC_ATLAS_STAGING_TENANT_ID ?? "",
  projectId: process.env.NEXT_PUBLIC_ATLAS_STAGING_PROJECT_ID ?? "",
  configured: Boolean(
    process.env.NEXT_PUBLIC_ATLAS_STAGING_TENANT_ID &&
      process.env.NEXT_PUBLIC_ATLAS_STAGING_PROJECT_ID
  )
};

export const LOGIN_WORKSPACES: readonly LoginWorkspace[] = [
  primaryWorkspace,
  ...(stagingWorkspace.configured ? [stagingWorkspace] : [])
];
