import type { components } from "@/shared/api/schema";

export type EnvironmentDto = components["schemas"]["Environment"];
export type TestRoleDto = components["schemas"]["TestRole"];
export type AccountPoolDto = components["schemas"]["AccountPool"];
export type AccountPoolCapacityDto =
  components["schemas"]["AccountPoolCapacity"];
export type TestAccountDto = components["schemas"]["TestAccount"];

export type IdentityWalletDto = {
  environment: EnvironmentDto | null;
  entries: Array<{
    role: TestRoleDto;
    pool: AccountPoolDto;
    capacity: AccountPoolCapacityDto;
    accounts: TestAccountDto[];
  }>;
};

export type IdentityCardViewModel = {
  id: string;
  roleId: string;
  roleRevision: number;
  poolId: string;
  name: string;
  roleKey: string;
  poolKey: string;
  description: string;
  capabilities: string[];
  environmentLabel: string;
  status: "READY" | "DEGRADED" | "DISABLED";
  available: number;
  leased: number;
  total: number;
  quarantined: number;
  cooldown: number;
  readyRatio: number;
  accountHints: string[];
};

export type IdentityWalletViewModel = {
  environment: {
    id: string;
    name: string;
    kind: string;
  } | null;
  identities: IdentityCardViewModel[];
  totals: {
    available: number;
    leased: number;
    quarantined: number;
  };
};
