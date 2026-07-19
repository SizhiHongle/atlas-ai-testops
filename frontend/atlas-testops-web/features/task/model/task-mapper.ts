import type {
  ExecutionUnitDto,
  ExecutionUnitViewModel,
  TaskPlanDto,
  TaskPlanVersionDto,
  TaskPlanVersionViewModel,
  TaskPlanViewModel,
  TaskRunDto,
  TaskRunViewModel,
  TaskUnitSummary
} from "./task";

export function mapTaskPlan(dto: TaskPlanDto): TaskPlanViewModel {
  return {
    id: dto.id,
    key: dto.taskKey,
    name: dto.name,
    status: dto.status,
    revision: dto.revision,
    updatedAt: new Date(dto.updatedAt)
  };
}

export function mapTaskPlanVersion(
  dto: TaskPlanVersionDto
): TaskPlanVersionViewModel {
  const matrixSize =
    dto.matrix.environmentIds.length *
    dto.matrix.browserProfileVersionIds.length *
    dto.matrix.identityProfileVersionIds.length *
    dto.matrix.dataProfileVersionIds.length *
    dto.pinnedCaseVersionIds.length;

  return {
    id: dto.id,
    taskPlanId: dto.taskPlanId,
    version: dto.version,
    versionRef: dto.versionRef,
    caseCount: dto.pinnedCaseVersionIds.length,
    matrixSize,
    contentDigest: dto.contentDigest,
    retryPolicyDigest: dto.policyDigests["infra-retry"] ?? null,
    publishedAt: new Date(dto.publishedAt)
  };
}

export function mapTaskRun(dto: TaskRunDto): TaskRunViewModel {
  return {
    id: dto.id,
    taskPlanVersionId: dto.taskPlanVersionId,
    lifecycle: dto.lifecycle,
    quality: dto.quality,
    hygiene: dto.hygiene,
    triggerSource: dto.triggerSource,
    materializationState: dto.materializationState,
    unitCount: dto.materializedUnitCount ?? null,
    revision: dto.revision,
    requestedAt: new Date(dto.requestedAt),
    startedAt: dto.startedAt ? new Date(dto.startedAt) : null,
    closedAt: dto.closedAt ? new Date(dto.closedAt) : null
  };
}

export function mapExecutionUnit(
  dto: ExecutionUnitDto
): ExecutionUnitViewModel {
  return {
    id: dto.id,
    ordinal: dto.ordinal,
    caseVersionId: dto.caseVersionId,
    environmentId: dto.environmentId,
    browserProfileVersionId: dto.browserProfileVersionId,
    lifecycle: dto.lifecycle,
    quality: dto.quality,
    hygiene: dto.hygiene
  };
}

export function summarizeExecutionUnits(
  units: ExecutionUnitViewModel[]
): TaskUnitSummary {
  const countQuality = (quality: string) =>
    units.filter((unit) => unit.quality === quality).length;
  const closed = units.filter((unit) => unit.lifecycle === "CLOSED").length;

  return {
    total: units.length,
    closed,
    running: units.filter((unit) => unit.lifecycle === "RUNNING").length,
    queued: units.filter((unit) => unit.lifecycle === "QUEUED").length,
    passed: countQuality("PASSED"),
    failed: countQuality("FAILED"),
    blocked: countQuality("BLOCKED"),
    infraError: countQuality("INFRA_ERROR"),
    canceled: countQuality("CANCELED"),
    progress: units.length ? Math.round((closed / units.length) * 100) : 0
  };
}
