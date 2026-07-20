"use client";

import {
  BadgeCheck,
  Bot,
  BrainCircuit,
  CheckCircle2,
  CircleStop,
  Component,
  FlaskConical,
  Grip,
  MousePointer2,
  Play,
  Plus,
  Radio,
  ShieldAlert,
  Sparkles,
  WandSparkles
} from "lucide-react";
import Link from "next/link";
import {
  usePathname,
  useRouter,
  useSearchParams
} from "next/navigation";
import { useState } from "react";

import { useSessionQuery } from "@/features/auth/api/auth-queries";
import { useFixtureCatalogQuery } from "@/features/fixture/api/fixture-queries";
import { useIdentityWalletQuery } from "@/features/identity/api/identity-queries";
import { ApiProblemError } from "@/shared/api/problem";
import { createRequestId } from "@/shared/api/request-id";
import { EmptyState } from "@/shared/ui/feedback/empty-state";
import { ErrorState } from "@/shared/ui/feedback/error-state";
import { LoadingState } from "@/shared/ui/feedback/loading-state";

import {
  useApplyWorkflowPatchMutation,
  useCancelDebugRunMutation,
  useCaseCatalogQuery,
  useCaseWorkspaceQuery,
  usePreviewWorkflowPatchMutation,
  useStartDebugRunMutation,
  useUpdateWorkflowLayoutMutation
} from "../api/case-queries";
import type {
  CaseWorkspaceViewModel,
  TestCaseCardViewModel,
  WorkflowNodeViewModel
} from "../model/case";
import {
  createHumanWorkflowPatch,
  type WorkflowPatchOperation
} from "../model/workflow-patch-builder";
import { CreateCaseDialog } from "./create-case-dialog";
import { PublishCaseDialog } from "./publish-case-dialog";
import { WorkflowCanvas } from "./workflow-canvas";
import { WorkflowPatchDialog } from "./workflow-patch-dialog";
import styles from "./case-page.module.css";

const CASE_AUTHORS = new Set(["ORG_ADMIN", "PROJECT_ADMIN", "CASE_AUTHOR"]);
const CASE_REVIEWERS = new Set([
  "ORG_ADMIN",
  "PROJECT_ADMIN",
  "CASE_REVIEWER"
]);
const RUN_OPERATORS = new Set([
  "ORG_ADMIN",
  "PROJECT_ADMIN",
  "RUN_OPERATOR",
  "CASE_AUTHOR"
]);
const PHASES = ["setup", "identity", "execute", "assert", "cleanup"] as const;

type WorkflowMode = "ai" | "manual";

function caseHref(
  pathname: string,
  searchParams: URLSearchParams,
  caseId: string
): string {
  const next = new URLSearchParams(searchParams);
  next.set("caseId", caseId);
  next.delete("nodeId");
  return `${pathname}?${next.toString()}`;
}

function problemMessage(error: unknown): string | null {
  if (error instanceof ApiProblemError) return error.problem.detail;
  return error instanceof Error ? error.message : null;
}

function currentPassedRun(workspace: CaseWorkspaceViewModel) {
  return (
    workspace.debugRuns.find(
      (run) =>
        run.lifecycle === "TERMINATED" &&
        run.outcome === "PASSED" &&
        run.snapshotStatus === "CURRENT"
    ) ?? null
  );
}

function caseState(
  workspace: CaseWorkspaceViewModel
): { label: string; tone: "good" | "violet" | "warn" | "neutral" } {
  const latestVersion = workspace.versions[0] ?? null;
  if (
    latestVersion &&
    latestVersion.semanticRevision === workspace.draft.semanticRevision
  ) {
    return { label: `v${latestVersion.version} 已发布`, tone: "good" };
  }
  if (currentPassedRun(workspace)) {
    return { label: "调试通过 · 待发布", tone: "violet" };
  }
  if (latestVersion) {
    return {
      label: `v${latestVersion.version} · 有草稿变更`,
      tone: "warn"
    };
  }
  return {
    label: workspace.draft.valid ? "草稿待调试" : "图需修正",
    tone: "neutral"
  };
}

function matchingPorts(
  source: WorkflowNodeViewModel,
  target: WorkflowNodeViewModel
) {
  for (const output of source.outputPorts) {
    const input = target.inputPorts.find(
      (candidate) =>
        candidate.semanticType === output.semanticType &&
        candidate.kind === output.kind
    );
    if (input) return { output, input };
  }
  return null;
}

function CaseStudio({
  projectId,
  testCase,
  environmentId,
  canAuthor,
  canRun,
  canReview
}: Readonly<{
  projectId: string;
  testCase: TestCaseCardViewModel;
  environmentId: string | null;
  canAuthor: boolean;
  canRun: boolean;
  canReview: boolean;
}>) {
  const pathname = usePathname();
  const router = useRouter();
  const searchParams = useSearchParams();
  const workspace = useCaseWorkspaceQuery(testCase.id);
  const startRun = useStartDebugRunMutation(testCase.id);
  const cancelRun = useCancelDebugRunMutation(testCase.id);
  const updateLayout = useUpdateWorkflowLayoutMutation(testCase.id);
  const previewPatch = usePreviewWorkflowPatchMutation(testCase.id);
  const applyPatch = useApplyWorkflowPatchMutation(testCase.id);
  const [publishOpen, setPublishOpen] = useState(false);
  const [patchOpen, setPatchOpen] = useState(false);
  const [workflowMode, setWorkflowMode] = useState<WorkflowMode>("ai");
  const [canvasError, setCanvasError] = useState<string | null>(null);

  if (workspace.isPending) {
    return <LoadingState label="正在读取 WorkflowDraft" />;
  }
  if (workspace.isError) {
    return (
      <ErrorState
        detail={workspace.error.message}
        onRetry={() => void workspace.refetch()}
      />
    );
  }
  if (!workspace.data) {
    return <LoadingState label="正在读取 WorkflowDraft" />;
  }

  const workspaceData = workspace.data;
  const latestRun = workspaceData.debugRuns[0] ?? null;
  const activeRun =
    latestRun && latestRun.lifecycle !== "TERMINATED" ? latestRun : null;
  const passedRun = currentPassedRun(workspaceData);
  const latestVersion = workspaceData.versions[0] ?? null;
  const currentVersion =
    latestVersion?.semanticRevision === workspaceData.draft.semanticRevision
      ? latestVersion
      : null;
  const state = caseState(workspaceData);
  const nodePhases = new Set(
    workspaceData.draft.nodes.map((node) => node.phase.toLowerCase())
  );
  const directPatchPending = previewPatch.isPending || applyPatch.isPending;
  const mutationError =
    startRun.error ??
    cancelRun.error ??
    updateLayout.error ??
    previewPatch.error ??
    applyPatch.error;
  const mutationErrorMessage =
    canvasError ?? problemMessage(mutationError);
  const canPublish =
    canReview &&
    workspaceData.draft.valid &&
    Boolean(passedRun) &&
    !currentVersion;
  const assetHref = `/projects/${projectId}/fixtures/assets?caseId=${testCase.id}`;

  function nodeHref(nodeId: string): string {
    const next = new URLSearchParams(searchParams.toString());
    next.set("nodeId", nodeId);
    return `${pathname}?${next.toString()}`;
  }

  async function handleStartDebugRun() {
    if (!environmentId) return;
    setCanvasError(null);
    try {
      const run = await startRun.mutateAsync({
        baseSemanticRevision: workspaceData.draft.semanticRevision,
        environmentId,
        executionDeadline: new Date(Date.now() + 15 * 60_000).toISOString()
      });
      router.push(
        `/projects/${projectId}/live?debugRunId=${run.id}&caseId=${testCase.id}`
      );
    } catch {
      // Mutation state renders the authoritative Problem Details.
    }
  }

  async function handleCancelDebugRun() {
    if (!activeRun) return;
    setCanvasError(null);
    try {
      await cancelRun.mutateAsync({
        runId: activeRun.id,
        revision: activeRun.revision,
        command: {
          clientMutationId: `cancel-debug-${createRequestId()}`,
          reason: "Canceled from Atlas case workbench."
        }
      });
    } catch {
      // Mutation state renders the authoritative Problem Details.
    }
  }

  async function handleNodeMove(nodeId: string, x: number, y: number) {
    setCanvasError(null);
    await updateLayout.mutateAsync({
      clientMutationId: `workflow-layout-${createRequestId()}`,
      baseLayoutRevision: workspaceData.draft.layoutRevision,
      source: "human",
      positions: {
        [nodeId]: { x, y }
      }
    });
  }

  async function applyCanvasOperation(
    operation: WorkflowPatchOperation,
    rationaleSummary: string
  ) {
    const command = createHumanWorkflowPatch(
      workspaceData.draft.semanticRevision,
      operation,
      rationaleSummary
    );
    setCanvasError(null);
    try {
      const preview = await previewPatch.mutateAsync(command);
      if (!preview.applicable) {
        throw new Error(
          preview.issues[0]?.message ?? "WorkflowPatch 未通过后端预检。"
        );
      }
      await applyPatch.mutateAsync(command);
    } catch (error) {
      setCanvasError(
        problemMessage(error) ?? "无法应用画布上的 WorkflowPatch。"
      );
      throw error;
    }
  }

  async function handleConnect(sourceNodeId: string, targetNodeId: string) {
    const source = workspaceData.draft.nodes.find(
      (node) => node.id === sourceNodeId
    );
    const target = workspaceData.draft.nodes.find(
      (node) => node.id === targetNodeId
    );
    if (!source || !target) {
      const error = new Error("无法读取连线两端的 Workflow Node。");
      setCanvasError(error.message);
      throw error;
    }
    const ports = matchingPorts(source, target);
    if (!ports) {
      const error = new Error(
        `${source.kind} 与 ${target.kind} 没有可自动匹配的 Typed Port。`
      );
      setCanvasError(error.message);
      throw error;
    }
    await applyCanvasOperation(
      {
        op: "ADD_EDGE",
        edge: {
          id: `edge-${createRequestId()}`,
          sourceNodeId,
          sourcePort: ports.output.key,
          targetNodeId,
          targetPort: ports.input.key,
          semanticType: ports.output.semanticType,
          kind: ports.output.kind,
          mapping: "direct"
        }
      },
      `Connect ${sourceNodeId} to ${targetNodeId} from the visual canvas.`
    );
  }

  async function handleDeleteEdge(edgeId: string) {
    await applyCanvasOperation(
      { op: "REMOVE_EDGE", edgeId },
      `Remove ${edgeId} from the visual canvas.`
    );
  }

  async function handleAutoLayout() {
    const positions: Record<string, { x: number; y: number }> = {};
    const levels = workspaceData.draft.executionLevels.length
      ? workspaceData.draft.executionLevels
      : [workspaceData.draft.nodes.map((node) => node.id)];
    levels.forEach((level, column) => {
      level.forEach((nodeId, row) => {
        positions[nodeId] = {
          x: 54 + column * 214,
          y: 54 + row * 142
        };
      });
    });
    setCanvasError(null);
    await updateLayout.mutateAsync({
      clientMutationId: `workflow-auto-layout-${createRequestId()}`,
      baseLayoutRevision: workspaceData.draft.layoutRevision,
      source: "human",
      positions
    });
  }

  const reviewTitle = !workspaceData.draft.valid
    ? `编排图还有 ${workspaceData.draft.issues.length} 个问题`
    : currentVersion
      ? "当前草稿已有发布快照"
      : passedRun
        ? "草稿已具备发布条件"
        : "当前 revision 需要重新调试";
  const reviewDescription = !workspaceData.draft.valid
    ? `${workspaceData.draft.issues[0]?.message ?? "请补齐有效 Workflow Node。"} 修正后才能启动真实调试。`
    : currentVersion
      ? "当前语义 Revision 已冻结为不可变 CaseVersion，Task 可以锁定该版本执行。"
      : passedRun
        ? "后端图验证与当前 DebugRun 均已通过。发布会冻结语义、依赖和审核摘要。"
        : "草稿还没有与当前语义 Revision 匹配的 PASSED DebugRun。";

  return (
    <>
      <section className={styles.studio}>
        <header className={styles.caseHeader}>
          <div>
            <span>{testCase.key}</span>
            <h2>{testCase.name}</h2>
            <p>
              Draft r{workspaceData.draft.semanticRevision} ·{" "}
              {testCase.updatedBy === "ai" ? "AI" : "人工"}最后编辑
            </p>
          </div>
          <div className={styles.versionLine}>
            <em data-tone={state.tone}>{state.label}</em>
            <span>
              {latestVersion
                ? `上次发布 v${latestVersion.version}`
                : "尚未发布"}
            </span>
          </div>
        </header>

        <div className={styles.modeSwitch}>
          <button
            className={workflowMode === "ai" ? styles.activeMode : ""}
            type="button"
            title="后端尚未开放 AI 生成 WorkflowPatch 的公共接口"
            onClick={() => setWorkflowMode("ai")}
          >
            <Sparkles size={14} /> AI 编排
          </button>
          <button
            className={workflowMode === "manual" ? styles.activeMode : ""}
            type="button"
            onClick={() => setWorkflowMode("manual")}
          >
            <Grip size={14} /> 人工编排
          </button>
          <span>
            共同编辑 Draft r{workspaceData.draft.semanticRevision}
            {updateLayout.isPending ? " · 正在保存布局" : ""}
          </span>
        </div>

        <section
          className={`${styles.intentPanel} ${
            workflowMode === "manual" ? styles.manualIntent : ""
          }`}
        >
          <div>
            {workflowMode === "ai" ? (
              <BrainCircuit size={18} />
            ) : (
              <MousePointer2 size={18} />
            )}
            <span>
              {workflowMode === "ai"
                ? "AI COPILOT · PATCH MODE"
                : "MANUAL CANVAS · DIRECT MODE"}
            </span>
          </div>
          <p>
            {workflowMode === "ai"
              ? testCase.summary
              : "拖拽保存布局；连接 Typed Port、删除连线或编辑节点参数，均会先经过后端预检，再原子写入同一份 WorkflowDraft。"}
          </p>
          <button
            type="button"
            disabled={workflowMode === "ai" || !canAuthor}
            title={
              workflowMode === "ai"
                ? "等待后端 AI WorkflowPatch 生成接口"
                : canAuthor
                  ? "打开人工 WorkflowPatch 编辑器"
                  : "需要 CASE_AUTHOR 权限"
            }
            onClick={() => setPatchOpen(true)}
          >
            {workflowMode === "ai" ? (
              <>
                生成编排 Patch <WandSparkles size={14} />
              </>
            ) : (
              <>
                编辑 WorkflowPatch <Grip size={14} />
              </>
            )}
          </button>
        </section>

        <div className={styles.phaseBar}>
          {PHASES.map((phase) => (
            <span data-filled={nodePhases.has(phase)} key={phase}>
              <i />
              {phase}
            </span>
          ))}
        </div>

        {mutationErrorMessage ? (
          <p className={styles.inlineError} role="alert">
            {mutationErrorMessage}
          </p>
        ) : null}

        <WorkflowCanvas
          key={`${workspaceData.draft.semanticRevision}:${workspaceData.draft.layoutRevision}`}
          nodes={workspaceData.draft.nodes}
          edges={workspaceData.draft.edges}
          width={workspaceData.draft.canvasWidth}
          height={workspaceData.draft.canvasHeight}
          selectedNodeId={searchParams.get("nodeId")}
          nodeHref={nodeHref}
          editable={
            workflowMode === "manual" &&
            canAuthor &&
            !updateLayout.isPending &&
            !directPatchPending
          }
          mode={workflowMode}
          draftRevision={workspaceData.draft.semanticRevision}
          assetHref={assetHref}
          onNodeMove={handleNodeMove}
          onConnect={handleConnect}
          onDeleteEdge={handleDeleteEdge}
          onAutoLayout={handleAutoLayout}
        />

        <div className={styles.actionDock}>
          <Link href={assetHref}>
            <Component size={15} /> 加入资产
          </Link>
          {activeRun ? (
            <>
              <Link
                className={styles.debugAction}
                href={`/projects/${projectId}/live?debugRunId=${activeRun.id}&caseId=${testCase.id}`}
              >
                <Play size={15} /> 进入 Debug 现场
              </Link>
              <button
                type="button"
                onClick={() => void handleCancelDebugRun()}
                disabled={cancelRun.isPending}
              >
                <CircleStop size={15} /> 取消 Debug
              </button>
            </>
          ) : (
            <button
              className={styles.debugAction}
              type="button"
              onClick={() => void handleStartDebugRun()}
              disabled={
                !canRun ||
                !environmentId ||
                !workspaceData.draft.valid ||
                startRun.isPending
              }
              title={
                !environmentId
                  ? "需要 ACTIVE Environment"
                  : !workspaceData.draft.valid
                    ? "WorkflowDraft 未通过验证"
                    : canRun
                      ? "启动真实 DebugRun"
                      : "需要 RUN_OPERATOR 权限"
              }
            >
              <Play size={15} />
              {workspaceData.draft.valid
                ? `实时调试 Draft r${workspaceData.draft.semanticRevision}`
                : "先修复依赖图"}
            </button>
          )}
          <button
            className={styles.publishAction}
            type="button"
            onClick={() => setPublishOpen(true)}
            disabled={!canPublish}
            title={
              !canReview
                ? "需要 CASE_REVIEWER 权限"
                : currentVersion
                  ? "当前语义 Revision 已发布"
                  : !passedRun
                    ? "需要当前 Revision 的 PASSED DebugRun"
                    : "发布不可变 CaseVersion"
            }
          >
            <BadgeCheck size={15} />
            {currentVersion
              ? `v${currentVersion.version} 已发布`
              : passedRun
                ? "发布新版本"
                : "调试后发布"}
          </button>
        </div>
      </section>

      <aside className={styles.review}>
        <span className={styles.reviewIcon}>
          {workspaceData.draft.valid ? (
            <CheckCircle2 size={24} />
          ) : (
            <ShieldAlert size={24} />
          )}
          <i />
        </span>
        <p>WORKFLOW REVIEW</p>
        <h2>{reviewTitle}</h2>
        <span className={styles.reviewDescription}>{reviewDescription}</span>

        <div className={styles.reviewChecks}>
          <span
            data-pending={
              workspaceData.draft.matchedRequiredInputs !==
              workspaceData.draft.totalRequiredInputs
            }
          >
            <CheckCircle2 size={13} />
            端口类型匹配
            <b>
              {workspaceData.draft.matchedRequiredInputs} /{" "}
              {workspaceData.draft.totalRequiredInputs}
            </b>
          </span>
          <span data-pending={!workspaceData.draft.valid}>
            <CheckCircle2 size={13} />
            数据依赖闭合
            <b>
              {workspaceData.draft.valid
                ? "CLOSED"
                : `${workspaceData.draft.issues.length} ISSUES`}
            </b>
          </span>
          <span data-pending={!passedRun}>
            <Radio size={13} />
            当前调试结果
            <b>{passedRun ? "PASSED" : latestRun?.outcome ?? "NOT_RUN"}</b>
          </span>
        </div>

        <article className={styles.versionCard}>
          <span>IMMUTABLE SNAPSHOT</span>
          <strong>{latestVersion ? `v${latestVersion.version}` : "—"}</strong>
          <small>
            {latestVersion
              ? `来自 Draft r${latestVersion.semanticRevision} · ${latestVersion.publishedAt.toLocaleString("zh-CN")}`
              : "等待第一次发布"}
          </small>
        </article>

        <button
          type="button"
          disabled
          title="等待后端 AI WorkflowPatch 生成接口"
        >
          <Bot size={14} /> 让 AI 增加负向分支 <Plus size={14} />
        </button>
      </aside>

      <PublishCaseDialog
        caseId={testCase.id}
        semanticRevision={workspaceData.draft.semanticRevision}
        debugRuns={workspaceData.debugRuns}
        open={publishOpen}
        onClose={() => setPublishOpen(false)}
      />
      <WorkflowPatchDialog
        caseId={testCase.id}
        semanticRevision={workspaceData.draft.semanticRevision}
        nodes={workspaceData.draft.nodes}
        edges={workspaceData.draft.edges}
        open={patchOpen}
        onClose={() => setPatchOpen(false)}
      />
    </>
  );
}

export function CasePage({ projectId }: Readonly<{ projectId: string }>) {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const session = useSessionQuery();
  const catalog = useCaseCatalogQuery(projectId);
  const identity = useIdentityWalletQuery(projectId);
  const fixtures = useFixtureCatalogQuery(projectId);
  const [createOpen, setCreateOpen] = useState(false);

  if (catalog.isPending || identity.isPending || fixtures.isPending) {
    return <LoadingState label="正在打开用例工作室" />;
  }
  const dependencyError =
    catalog.error ?? identity.error ?? fixtures.error ?? null;
  if (dependencyError) {
    return (
      <ErrorState
        detail={dependencyError.message}
        onRetry={() => {
          void catalog.refetch();
          void identity.refetch();
          void fixtures.refetch();
        }}
      />
    );
  }
  if (!catalog.data || !identity.data || !fixtures.data) {
    return <LoadingState label="正在打开用例工作室" />;
  }

  const selectedId = searchParams.get("caseId");
  const selected =
    catalog.data.find((item) => item.id === selectedId) ??
    catalog.data[0] ??
    null;
  const preferredRoleId = searchParams.get("roleId");
  const preferredBlueprintId = searchParams.get("blueprintId");
  const preferredIdentity = identity.data.identities.find(
    (item) => item.roleId === preferredRoleId
  );
  const preferredBlueprint = fixtures.data.blueprints.find(
    (item) => item.id === preferredBlueprintId
  );
  const canAuthor =
    session.data?.roles.some((role) => CASE_AUTHORS.has(role)) ?? false;
  const canReview =
    session.data?.roles.some((role) => CASE_REVIEWERS.has(role)) ?? false;
  const canRun =
    session.data?.roles.some((role) => RUN_OPERATORS.has(role)) ?? false;

  return (
    <div className={styles.page}>
      <header className={styles.hero}>
        <div>
          <p>
            <FlaskConical size={13} /> CASE WORKBENCH
          </p>
          <h1>用例，才是编排真正的容器。</h1>
          <span>
            AI 与人工共同编辑同一份 WorkflowDraft；调试草稿，发布快照，再交给 Task 锁定执行。
          </span>
        </div>
        <button
          type="button"
          disabled={!canAuthor}
          title={canAuthor ? "创建真实 TestCase" : "需要 CASE_AUTHOR 权限"}
          onClick={() => setCreateOpen(true)}
        >
          <Plus size={16} /> 新建用例
        </button>
      </header>

      {preferredIdentity || preferredBlueprint ? (
        <div className={styles.contextBanner}>
          <span>待绑定上下文</span>
          {preferredIdentity ? (
            <i>
              身份 · {preferredIdentity.name} / {preferredIdentity.roleKey}
            </i>
          ) : null}
          {preferredBlueprint ? (
            <i>
              资产 · {preferredBlueprint.name} / {preferredBlueprint.version}
            </i>
          ) : null}
          <strong>选择或新建用例后，由 WorkflowPatch 明确写入草稿。</strong>
        </div>
      ) : null}

      {!selected ? (
        <EmptyState
          title="还没有测试用例"
          detail="创建 TestCase 后，后端会在同一事务中建立唯一 WorkflowDraft。"
        />
      ) : (
        <section className={styles.workbench}>
          <aside className={styles.caseList}>
            <header>
              <span>TEST CASES</span>
              <strong>{catalog.data.length}</strong>
            </header>
            <div>
              {catalog.data.map((testCase) => (
                <Link
                  className={testCase.id === selected.id ? styles.active : ""}
                  href={caseHref(
                    pathname,
                    new URLSearchParams(searchParams.toString()),
                    testCase.id
                  )}
                  key={testCase.id}
                >
                  <span>{testCase.key}</span>
                  <strong>{testCase.name}</strong>
                  <small>
                    {testCase.primaryRoleKey ?? `${testCase.actorCount} 个身份`} ·
                    Draft r{testCase.semanticRevision}
                  </small>
                  <i data-valid={testCase.graphValid} />
                  <em>
                    {testCase.graphValid ? "图验证通过" : "图需修正"}
                  </em>
                </Link>
              ))}
            </div>
            <button
              type="button"
              disabled={!canAuthor}
              onClick={() => setCreateOpen(true)}
            >
              <Plus size={14} /> 创建空白用例
            </button>
          </aside>

          <CaseStudio
            projectId={projectId}
            testCase={selected}
            environmentId={identity.data.environment?.id ?? null}
            canAuthor={canAuthor}
            canRun={canRun}
            canReview={canReview}
          />
        </section>
      )}

      <CreateCaseDialog
        projectId={projectId}
        identities={identity.data.identities}
        preferredRoleId={preferredRoleId}
        open={createOpen}
        onClose={() => setCreateOpen(false)}
      />
    </div>
  );
}
