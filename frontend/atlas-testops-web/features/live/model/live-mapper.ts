import type {
  LiveSnapshotViewModel,
  UnitAttemptDto,
  UnitAttemptLiveSnapshotDto,
  UnitAttemptViewModel
} from "./live";

export function mapUnitAttempt(dto: UnitAttemptDto): UnitAttemptViewModel {
  return {
    id: dto.id,
    attemptNumber: dto.attemptNumber,
    lifecycle: dto.lifecycle,
    quality: dto.quality,
    hygiene: dto.hygiene,
    revision: dto.revision,
    executionDeadline: new Date(dto.executionDeadline),
    startedAt: dto.startedAt ? new Date(dto.startedAt) : null,
    closedAt: dto.closedAt ? new Date(dto.closedAt) : null
  };
}

export function mapLiveSnapshot(
  dto: UnitAttemptLiveSnapshotDto
): LiveSnapshotViewModel {
  return {
    session: {
      id: dto.session.id,
      unitAttemptId: dto.session.unitAttemptId,
      browserSessionId: dto.session.browserSessionId,
      state: dto.session.state,
      controlEpoch: dto.session.controlEpoch,
      fencingToken: dto.session.fencingToken,
      browserRevision: dto.session.browserRevision,
      revision: dto.session.revision,
      humanInfluenced: dto.session.humanInfluenced,
      updatedAt: new Date(dto.session.updatedAt)
    },
    lease: dto.lease
      ? {
          ownerId: dto.lease.ownerId,
          ownerType: dto.lease.ownerType,
          state: dto.lease.state,
          expiresAt: new Date(dto.lease.expiresAt)
        }
      : null,
    pendingCommand: dto.pendingCommand
      ? {
          id: dto.pendingCommand.id,
          type: dto.pendingCommand.commandType,
          status: dto.pendingCommand.status,
          reason: dto.pendingCommand.reason
        }
      : null,
    observedAt: new Date(dto.observedAt)
  };
}
