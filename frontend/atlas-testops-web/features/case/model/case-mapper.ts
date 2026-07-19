import type {
  CaseVersionDto,
  CaseVersionViewModel,
  CaseWorkspaceViewModel,
  DebugRunDto,
  DebugRunViewModel,
  TestCaseCardViewModel,
  TestCaseCatalogItemDto,
  WorkflowDraftSnapshotDto,
  WorkflowPatchPreviewDto,
  WorkflowPatchPreviewViewModel,
  WorkflowPortViewModel
} from "./case";

function mapPort(
  port: WorkflowDraftSnapshotDto["graph"]["nodes"][number]["inputPorts"][number]
): WorkflowPortViewModel {
  return {
    key: port.key,
    semanticType: port.semanticType,
    kind: port.kind,
    required: port.required,
    sensitive: port.sensitive
  };
}

export function mapTestCase(
  dto: TestCaseCatalogItemDto
): TestCaseCardViewModel {
  return {
    id: dto.id,
    draftId: dto.draftId,
    key: dto.caseKey,
    name: dto.name,
    summary: dto.intent.summary,
    status: dto.status,
    graphValid: dto.graphValid,
    semanticRevision: dto.semanticRevision,
    layoutRevision: dto.layoutRevision,
    actorCount: dto.intent.actors.length,
    updatedBy: dto.updatedBy,
    updatedAt: new Date(dto.updatedAt)
  };
}

function mapDebugRun(dto: DebugRunDto): DebugRunViewModel {
  return {
    id: dto.id,
    lifecycle: dto.lifecycle,
    outcome: dto.outcome,
    snapshotStatus: dto.snapshotStatus,
    revision: dto.revision,
    requestedAt: new Date(dto.requestedAt),
    completedAt: dto.completedAt ? new Date(dto.completedAt) : null,
    failureDetail: dto.failureDetail ?? null
  };
}

function mapCaseVersion(dto: CaseVersionDto): CaseVersionViewModel {
  return {
    id: dto.id,
    version: dto.version,
    revision: dto.revision,
    publishedAt: new Date(dto.publishedAt),
    reviewSummary: dto.reviewSummary,
    semanticRevision: dto.semanticRevision
  };
}

export function mapCaseWorkspace(
  draft: WorkflowDraftSnapshotDto,
  debugRuns: DebugRunDto[],
  versions: CaseVersionDto[]
): CaseWorkspaceViewModel {
  const nodes = draft.graph.nodes.map((node, index) => {
    const layout = draft.layout[node.id];
    return {
      id: node.id,
      kind: node.kind,
      phase: node.phase,
      versionRef: node.versionRef,
      terminal: node.terminal,
      oracleStrength: node.oracleStrength ?? null,
      inputPorts: node.inputPorts.map(mapPort),
      outputPorts: node.outputPorts.map(mapPort),
      x: layout?.x ?? (index % 3) * 220 + 30,
      y: layout?.y ?? Math.floor(index / 3) * 150 + 30
    };
  });
  const canvasWidth = Math.max(720, ...nodes.map((node) => node.x + 210));
  const canvasHeight = Math.max(430, ...nodes.map((node) => node.y + 140));

  return {
    draft: {
      id: draft.id,
      semanticRevision: draft.semanticRevision,
      layoutRevision: draft.layoutRevision,
      semanticDigest: draft.semanticDigest,
      valid: draft.validation.valid,
      issues: draft.validation.issues.map((issue) => ({
        code: issue.code,
        message: issue.message,
        nodeId: issue.nodeId ?? null
      })),
      executionLevels: draft.validation.executionLevels.map((level) => [
        ...level
      ]),
      nodes,
      edges: draft.graph.edges.map((edge) => ({
        id: edge.id,
        sourceNodeId: edge.sourceNodeId,
        targetNodeId: edge.targetNodeId,
        semanticType: edge.semanticType
      })),
      canvasWidth,
      canvasHeight
    },
    debugRuns: debugRuns
      .map(mapDebugRun)
      .sort((left, right) => right.requestedAt.getTime() - left.requestedAt.getTime()),
    versions: versions
      .map(mapCaseVersion)
      .sort((left, right) => right.publishedAt.getTime() - left.publishedAt.getTime())
  };
}

export function mapWorkflowPatchPreview(
  preview: WorkflowPatchPreviewDto
): WorkflowPatchPreviewViewModel {
  return {
    applicable: preview.applicable,
    semanticDigest: preview.semanticDigest,
    graphValid: preview.validation.valid,
    nodeCount: preview.graph.nodes.length,
    edgeCount: preview.graph.edges.length,
    issues: [
      ...preview.issues.map((issue) => ({
        code: issue.code,
        message: issue.message
      })),
      ...preview.validation.issues.map((issue) => ({
        code: issue.code,
        message: issue.message
      }))
    ]
  };
}
