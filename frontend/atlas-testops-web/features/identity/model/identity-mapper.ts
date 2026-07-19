import type {
  IdentityCardViewModel,
  IdentityWalletDto,
  IdentityWalletViewModel
} from "./identity";

function identityStatus(
  poolStatus: "ACTIVE" | "DISABLED",
  available: number,
  quarantined: number
): IdentityCardViewModel["status"] {
  if (poolStatus === "DISABLED") return "DISABLED";
  if (available === 0 || quarantined > 0) return "DEGRADED";
  return "READY";
}

export function mapIdentityWallet(
  dto: IdentityWalletDto
): IdentityWalletViewModel {
  const environment = dto.environment
    ? {
        id: dto.environment.id,
        name: dto.environment.name,
        kind: dto.environment.kind
      }
    : null;

  const identities = dto.entries.map(
    ({ role, pool, capacity, accounts }): IdentityCardViewModel => ({
      id: `${role.id}:${pool.id}`,
      roleId: role.id,
      roleRevision: role.revision,
      poolId: pool.id,
      name: role.name,
      roleKey: role.roleKey,
      poolKey: pool.poolKey,
      description: role.description,
      capabilities: [...role.capabilities],
      environmentLabel: environment
        ? `${environment.name} / ${environment.kind}`
        : "未连接环境",
      status: identityStatus(
        pool.status,
        capacity.availableSlots,
        capacity.quarantinedAccounts
      ),
      available: capacity.availableSlots,
      leased: capacity.leasedSlots,
      total: capacity.totalSlots,
      quarantined: capacity.quarantinedAccounts,
      cooldown: capacity.cooldownAccounts,
      readyRatio:
        capacity.totalSlots > 0
          ? capacity.availableSlots / capacity.totalSlots
          : 0,
      accountHints: accounts
        .filter((account) => account.lifecycleStatus === "ACTIVE")
        .slice(0, 4)
        .map((account) => account.loginHintMasked)
    })
  );

  return {
    environment,
    identities,
    totals: identities.reduce(
      (totals, item) => ({
        available: totals.available + item.available,
        leased: totals.leased + item.leased,
        quarantined: totals.quarantined + item.quarantined
      }),
      { available: 0, leased: 0, quarantined: 0 }
    )
  };
}
