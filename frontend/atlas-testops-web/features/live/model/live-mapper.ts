import type {
  DebugEventWindowViewModel,
  DebugEvidenceViewModel,
  DebugLiveEventDto,
  DebugLiveEventViewModel,
  DebugLiveSnapshotDto,
  DebugLiveSnapshotViewModel,
  DebugRunDetailViewModel,
  DebugRunDto,
  DebugRunEventDto,
  EvidenceManifestDto,
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
    queuedAt: new Date(dto.queuedAt),
    startedAt: dto.startedAt ? new Date(dto.startedAt) : null,
    closedAt: dto.closedAt ? new Date(dto.closedAt) : null,
    finalizedAt: dto.finalizedAt ? new Date(dto.finalizedAt) : null,
    cleanupResolvedAt: dto.cleanupResolvedAt
      ? new Date(dto.cleanupResolvedAt)
      : null,
    createdAt: new Date(dto.createdAt),
    updatedAt: new Date(dto.updatedAt)
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

export function mapDebugLiveEvent(
  dto: DebugLiveEventDto
): DebugLiveEventViewModel {
  return {
    id: dto.eventId,
    seq: dto.seq,
    type: dto.eventType,
    lifecycle: dto.lifecycle,
    outcome: dto.outcome,
    snapshotStatus: dto.snapshotStatus,
    data: { ...dto.data },
    occurredAt: new Date(dto.occurredAt),
    cursor: dto.cursor
  };
}

export function mapDebugRunEvent(
  dto: DebugRunEventDto
): DebugLiveEventViewModel {
  return {
    id: dto.id,
    seq: dto.seq,
    type: dto.eventType,
    lifecycle: dto.lifecycle,
    outcome: dto.outcome,
    snapshotStatus: dto.snapshotStatus,
    data: { ...dto.payload },
    occurredAt: new Date(dto.occurredAt),
    cursor: ""
  };
}

export function mapDebugLiveSnapshot(
  dto: DebugLiveSnapshotDto
): DebugLiveSnapshotViewModel {
  return {
    run: {
      id: dto.run.debugRunId,
      projectId: dto.run.projectId,
      testCaseId: dto.run.testCaseId,
      environmentId: dto.run.environmentId,
      lifecycle: dto.run.lifecycle,
      outcome: dto.run.outcome,
      snapshotStatus: dto.run.snapshotStatus,
      revision: dto.run.revision,
      executionDeadline: new Date(dto.run.executionDeadline),
      startedAt: dto.run.startedAt ? new Date(dto.run.startedAt) : null,
      completedAt: dto.run.completedAt ? new Date(dto.run.completedAt) : null,
      cancelRequestedAt: dto.run.cancelRequestedAt
        ? new Date(dto.run.cancelRequestedAt)
        : null
    },
    cursor: dto.cursor,
    latestEvent: dto.latestEvent
      ? mapDebugLiveEvent(dto.latestEvent)
      : null,
    observedAt: new Date(dto.observedAt)
  };
}

export function mapDebugRunDetail(
  dto: DebugRunDto
): DebugRunDetailViewModel {
  const literalKeyword = dto.testIr.variables.searchKeyword;
  const searchKeyword =
    literalKeyword?.kind === "LITERAL" &&
    typeof literalKeyword.value === "string"
      ? literalKeyword.value
      : null;
  const nodeDetails = new Map(
    dto.testIr.workflow.nodes.map((node) => [node.id, node])
  );

  function readableNode(node: DebugRunDto["planTemplate"]["nodes"][number]) {
    const detail = nodeDetails.get(node.nodeId);
    if (node.versionRef.includes("surface-open")) {
      return {
        title: "打开百度首页",
        description: "进入已审核的百度搜索页面"
      };
    }
    if (node.versionRef.includes("semantic-search")) {
      return {
        title: `搜索 ${searchKeyword ?? "冻结关键词"}`,
        description: "识别搜索框、输入关键词并提交"
      };
    }
    if (node.versionRef.includes("assert.search-results")) {
      return {
        title: "验证搜索结果",
        description: "检查页面是否保留或展示冻结关键词"
      };
    }
    if (node.kind.toLowerCase() === "fixture") {
      return {
        title: "准备测试数据",
        description: "创建本次运行专用的隔离数据"
      };
    }
    if (node.kind.toLowerCase() === "cleanup") {
      return {
        title: "释放运行资源",
        description: "清理 Fixture、Session 与账号租约"
      };
    }
    return {
      title: detail?.kind ?? node.kind,
      description: `执行已冻结组件 ${node.versionRef}`
    };
  }

  return {
    id: dto.id,
    testCaseId: dto.testCaseId,
    environmentId: dto.environmentId,
    semanticRevision: dto.semanticRevision,
    lifecycle: dto.lifecycle,
    outcome: dto.outcome,
    snapshotStatus: dto.snapshotStatus,
    revision: dto.revision,
    requestedAt: new Date(dto.requestedAt),
    startedAt: dto.startedAt ? new Date(dto.startedAt) : null,
    completedAt: dto.completedAt ? new Date(dto.completedAt) : null,
    executionDeadline: new Date(dto.executionDeadline),
    planDigest: dto.planDigest,
    planNodes: dto.planTemplate.nodes
      .map((node) => {
        const readable = readableNode(node);
        return {
          id: node.nodeId,
          kind: node.kind,
          versionRef: node.versionRef,
          executionLevel: node.executionLevel,
          title: readable.title,
          description: readable.description
        };
      })
      .sort(
        (left, right) =>
          left.executionLevel - right.executionLevel ||
          left.id.localeCompare(right.id)
      ),
    searchKeyword
  };
}

export function mapDebugEventWindow(
  events: DebugRunEventDto[],
  truncated: boolean
): DebugEventWindowViewModel {
  return {
    items: events
      .map(mapDebugRunEvent)
      .sort((left, right) => left.seq - right.seq),
    truncated
  };
}

export function mapDebugEvidence(
  dto: EvidenceManifestDto
): DebugEvidenceViewModel {
  return {
    id: dto.id,
    outcome: dto.outcome,
    completeness: dto.completeness,
    integrity: dto.integrity,
    eventCount: dto.eventCount,
    passedAssertions: dto.passedAssertions,
    failedAssertions: dto.failedAssertions,
    inconclusiveAssertions: dto.inconclusiveAssertions,
    artifacts: dto.artifacts
      .map((artifact) => ({
        id: artifact.id,
        kind: artifact.kind,
        mimeType: artifact.mimeType,
        sizeBytes: artifact.sizeBytes,
        integrity: artifact.integrity,
        required: artifact.required,
        capturedAt: new Date(artifact.capturedAt),
        contentDigest: artifact.contentDigest
      }))
      .sort(
        (left, right) =>
          right.capturedAt.getTime() - left.capturedAt.getTime()
      ),
    assertions: dto.assertionResults.map((assertion) => ({
      id: assertion.id,
      nodeId: assertion.nodeId,
      status: assertion.status,
      strength: assertion.strength,
      summary: assertion.actualSafeSummary,
      durationMs: assertion.durationMs,
      evidenceRefs: [...assertion.evidenceRefs]
    })),
    finalizedAt: new Date(dto.finalizedAt),
    contentDigest: dto.contentDigest
  };
}
