"use client";

import {
  Bot,
  CheckCircle2,
  CircleStop,
  FlaskConical,
  GitBranch,
  Grip,
  Play,
  Plus,
  Rocket,
  ShieldAlert
} from "lucide-react";
import Link from "next/link";
import {
  usePathname,
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
  useCancelDebugRunMutation,
  useUpdateWorkflowLayoutMutation,
  useCaseCatalogQuery,
  useCaseWorkspaceQuery,
  useStartDebugRunMutation
} from "../api/case-queries";
import type { TestCaseCardViewModel } from "../model/case";
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

function CaseStudio({
  testCase,
  environmentId,
  canAuthor,
  canRun,
  canReview
}: Readonly<{
  testCase: TestCaseCardViewModel;
  environmentId: string | null;
  canAuthor: boolean;
  canRun: boolean;
  canReview: boolean;
}>) {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const workspace = useCaseWorkspaceQuery(testCase.id);
  const startRun = useStartDebugRunMutation(testCase.id);
  const cancelRun = useCancelDebugRunMutation(testCase.id);
  const updateLayout = useUpdateWorkflowLayoutMutation(testCase.id);
  const [publishOpen, setPublishOpen] = useState(false);
  const [patchOpen, setPatchOpen] = useState(false);

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
  const selectedNodeId = searchParams.get("nodeId");
  const selectedNode =
    workspaceData.draft.nodes.find((node) => node.id === selectedNodeId) ??
    workspaceData.draft.nodes[0] ??
    null;
  const mutationError =
    startRun.error ?? cancelRun.error ?? updateLayout.error;
  const mutationErrorMessage =
    mutationError instanceof ApiProblemError
      ? mutationError.problem.detail
      : mutationError?.message;

  function nodeHref(nodeId: string): string {
    const next = new URLSearchParams(searchParams.toString());
    next.set("nodeId", nodeId);
    return `${pathname}?${next.toString()}`;
  }

  async function handleStartDebugRun() {
    if (!environmentId) return;
    await startRun.mutateAsync({
      baseSemanticRevision: workspaceData.draft.semanticRevision,
      environmentId,
      executionDeadline: new Date(Date.now() + 15 * 60_000).toISOString()
    });
  }

  async function handleCancelDebugRun() {
    if (!activeRun) return;
    await cancelRun.mutateAsync({
      runId: activeRun.id,
      revision: activeRun.revision,
      command: {
        clientMutationId: `cancel-debug-${createRequestId()}`,
        reason: "Canceled from Atlas case workbench."
      }
    });
  }

  async function handleNodeMove(nodeId: string, x: number, y: number) {
    await updateLayout.mutateAsync({
      clientMutationId: `workflow-layout-${createRequestId()}`,
      baseLayoutRevision: workspaceData.draft.layoutRevision,
      source: "human",
      positions: {
        [nodeId]: { x, y }
      }
    });
  }

  return (
    <>
      <section className={styles.studio}>
        <header className={styles.caseHeader}>
          <div>
            <span>{testCase.key}</span>
            <h2>{testCase.name}</h2>
            <p>
              Draft r{workspaceData.draft.semanticRevision} ·{" "}
              {testCase.actorCount} 个身份 · {testCase.summary}
            </p>
          </div>
          <em data-valid={workspaceData.draft.valid}>
            {workspaceData.draft.valid ? "图验证通过" : "图仍需修正"}
          </em>
        </header>

        <div className={styles.toolbar}>
          <button
            type="button"
            disabled
            title="后端尚未开放 AI 生成 WorkflowPatch 的公共接口"
          >
            <Bot size={15} /> AI 编排
          </button>
          <button
            type="button"
            onClick={() => setPatchOpen(true)}
            disabled={!canAuthor}
            title={
              canAuthor
                ? "预检并原子应用人工 WorkflowPatch"
                : "需要 CASE_AUTHOR 权限"
            }
          >
            <Grip size={15} /> 人工编排
          </button>
          <span>
            语义 Revision {workspaceData.draft.semanticRevision} · 布局 Revision{" "}
            {workspaceData.draft.layoutRevision}
            {updateLayout.isPending ? " · 正在保存布局" : ""}
          </span>
          {activeRun ? (
            <button
              type="button"
              onClick={() => void handleCancelDebugRun()}
              disabled={cancelRun.isPending}
            >
              <CircleStop size={15} /> 取消 Debug
            </button>
          ) : (
            <button
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
                    : "启动真实 DebugRun"
              }
            >
              <Play size={15} /> 运行草稿
            </button>
          )}
          <button
            type="button"
            onClick={() => setPublishOpen(true)}
            disabled={!canReview}
            title={canReview ? "发布不可变 CaseVersion" : "需要用例审核权限"}
          >
            <Rocket size={15} /> 发布
          </button>
        </div>

        {mutationErrorMessage ? (
          <p className={styles.inlineError} role="alert">
            {mutationErrorMessage}
          </p>
        ) : null}

        <div className={styles.phaseBar}>
          {["SETUP", "IDENTITY", "EXECUTE", "ASSERT", "CLEANUP"].map(
            (phase) => (
              <span key={phase}>{phase}</span>
            )
          )}
        </div>

        <section className={styles.graphPanel}>
          <header>
            <span>
              <GitBranch size={15} /> WORKFLOW GRAPH
            </span>
            <i>
              {workspaceData.draft.nodes.length} NODES ·{" "}
              {workspaceData.draft.edges.length} EDGES
            </i>
          </header>
          <WorkflowCanvas
            key={`${workspaceData.draft.semanticRevision}:${workspaceData.draft.layoutRevision}`}
            nodes={workspaceData.draft.nodes}
            edges={workspaceData.draft.edges}
            width={workspaceData.draft.canvasWidth}
            height={workspaceData.draft.canvasHeight}
            selectedNodeId={selectedNodeId}
            nodeHref={nodeHref}
            editable={canAuthor && !updateLayout.isPending}
            onNodeMove={handleNodeMove}
          />
        </section>
      </section>

      <aside className={styles.review}>
        <span className={styles.reviewIcon}>
          {workspaceData.draft.valid ? (
            <CheckCircle2 size={24} />
          ) : (
            <ShieldAlert size={24} />
          )}
        </span>
        <p>WORKFLOW REVIEW</p>
        <h2>
          {workspaceData.draft.valid
            ? "草稿已通过图验证"
            : "草稿还不能进入执行"}
        </h2>
        <span className={styles.reviewDescription}>
          {workspaceData.draft.valid
            ? "端口、DAG 与断言覆盖均由后端验证结果确认。"
            : workspaceData.draft.issues[0]?.message ??
              "请补齐至少一个有效 Workflow Node。"}
        </span>

        <div className={styles.reviewFacts}>
          <div>
            <span>验证问题</span>
            <strong>{workspaceData.draft.issues.length}</strong>
          </div>
          <div>
            <span>当前调试结果</span>
            <strong>{latestRun?.outcome ?? "NOT_RUN"}</strong>
          </div>
          <div>
            <span>已发布版本</span>
            <strong>{workspaceData.versions.length}</strong>
          </div>
        </div>

        {selectedNode ? (
          <article className={styles.nodeDetail}>
            <span>SELECTED NODE</span>
            <strong>{selectedNode.kind}</strong>
            <small>
              {selectedNode.phase} · {selectedNode.versionRef}
            </small>
            <p>
              {selectedNode.inputPorts.length} 输入 /{" "}
              {selectedNode.outputPorts.length} 输出
              {selectedNode.oracleStrength
                ? ` · ${selectedNode.oracleStrength} Oracle`
                : ""}
            </p>
          </article>
        ) : null}

        {workspaceData.versions[0] ? (
          <article className={styles.versionCard}>
            <span>IMMUTABLE SNAPSHOT</span>
            <strong>v{workspaceData.versions[0].version}</strong>
            <small>
              {workspaceData.versions[0].publishedAt.toLocaleString("zh-CN")}
            </small>
          </article>
        ) : null}
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
            AI 与人工共同编辑 WorkflowDraft；调试草稿、发布快照，再交给 Task 锁定执行。
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
                    Draft r{testCase.semanticRevision} ·{" "}
                    {testCase.graphValid ? "VALID" : "INVALID"}
                  </small>
                  <em>{testCase.updatedBy}</em>
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
