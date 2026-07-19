"use client";

import {
  ArrowRight,
  ArrowUpRight,
  Bot,
  CircleAlert,
  CircleCheck,
  CircleStop,
  Globe2,
  ListChecks,
  Pause,
  Play,
  Plus,
  Radio,
  Rocket,
  Search
} from "lucide-react";
import Link from "next/link";
import {
  usePathname,
  useSearchParams
} from "next/navigation";
import {
  useMemo,
  useState,
  type CSSProperties
} from "react";

import { useSessionQuery } from "@/features/auth/api/auth-queries";
import { ApiProblemError } from "@/shared/api/problem";
import { createRequestId } from "@/shared/api/request-id";
import { EmptyState } from "@/shared/ui/feedback/empty-state";
import { ErrorState } from "@/shared/ui/feedback/error-state";
import { LoadingState } from "@/shared/ui/feedback/loading-state";

import {
  useExecutionUnitsQuery,
  useTaskPlansQuery,
  useTaskPlanVersionsQuery,
  useTaskRunCommandMutation,
  useTaskRunsQuery
} from "../api/task-queries";
import { summarizeExecutionUnits } from "../model/task-mapper";
import type {
  TaskPlanVersionViewModel,
  TaskRunViewModel
} from "../model/task";
import { CreateTaskPlanDialog } from "./create-task-plan-dialog";
import { StartTaskRunDialog } from "./start-task-run-dialog";
import styles from "./task-page.module.css";

const RUN_OPERATORS = new Set([
  "ORG_ADMIN",
  "PROJECT_ADMIN",
  "RUN_OPERATOR"
]);

type ProgressStyle = CSSProperties & { "--progress": string };

const DATE_FORMAT = new Intl.DateTimeFormat("zh-CN", {
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit"
});

function shortId(value: string): string {
  return value.slice(0, 8).toUpperCase();
}

function statusTone(run: TaskRunViewModel): "good" | "risk" | "active" | "muted" {
  if (run.lifecycle !== "CLOSED") return "active";
  if (run.quality === "PASSED") return "good";
  if (["FAILED", "BLOCKED", "INFRA_ERROR"].includes(run.quality)) return "risk";
  return "muted";
}

function searchHref(
  pathname: string,
  searchParams: URLSearchParams,
  updates: Record<string, string | null>
): string {
  const next = new URLSearchParams(searchParams.toString());
  Object.entries(updates).forEach(([key, value]) => {
    if (value) next.set(key, value);
    else next.delete(key);
  });
  return `${pathname}?${next.toString()}`;
}

function RunCard({
  run,
  version,
  href,
  selected
}: Readonly<{
  run: TaskRunViewModel;
  version: TaskPlanVersionViewModel | null;
  href: string;
  selected: boolean;
}>) {
  return (
    <Link
      className={`${styles.runCard} ${selected ? styles.selectedRun : ""}`}
      data-tone={statusTone(run)}
      href={href}
    >
      <i />
      <div>
        <span>
          RUN-{shortId(run.id)} · {run.triggerSource}
        </span>
        <strong>{version?.versionRef ?? `VERSION-${shortId(run.taskPlanVersionId)}`}</strong>
      </div>
      <b>{run.lifecycle}</b>
      <small>{run.quality}</small>
      <em>{DATE_FORMAT.format(run.requestedAt)}</em>
      <ArrowUpRight size={15} aria-hidden="true" />
    </Link>
  );
}

export function TaskPage({ projectId }: Readonly<{ projectId: string }>) {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const session = useSessionQuery();
  const plans = useTaskPlansQuery(projectId);
  const runs = useTaskRunsQuery(projectId);
  const [createPlanOpen, setCreatePlanOpen] = useState(false);
  const [startRunOpen, setStartRunOpen] = useState(false);

  const requestedPlanId = searchParams.get("planId");
  const selectedPlanId =
    plans.data?.find((plan) => plan.id === requestedPlanId)?.id ??
    plans.data?.[0]?.id ??
    null;
  const versions = useTaskPlanVersionsQuery(selectedPlanId);
  const requestedVersionId = searchParams.get("versionId");
  const selectedVersionId =
    versions.data?.find((version) => version.id === requestedVersionId)?.id ??
    versions.data?.[0]?.id ??
    null;
  const selectedVersion =
    versions.data?.find((version) => version.id === selectedVersionId) ??
    versions.data?.[0] ??
    null;
  const versionIds = useMemo(
    () => new Set(versions.data?.map((version) => version.id) ?? []),
    [versions.data]
  );
  const planRuns = useMemo(
    () => runs.data?.filter((run) => versionIds.has(run.taskPlanVersionId)) ?? [],
    [runs.data, versionIds]
  );
  const query = searchParams.get("q")?.trim().toLowerCase() ?? "";
  const visibleRuns = query
    ? planRuns.filter((run) =>
        [
          run.id,
          run.lifecycle,
          run.quality,
          run.triggerSource,
          versions.data?.find((version) => version.id === run.taskPlanVersionId)
            ?.versionRef ?? ""
        ].some((value) => value.toLowerCase().includes(query))
      )
    : planRuns;
  const selectedRunId =
    searchParams.get("runId") ?? visibleRuns[0]?.id ?? planRuns[0]?.id ?? null;
  const selectedRun =
    planRuns.find((run) => run.id === selectedRunId) ?? planRuns[0] ?? null;
  const selectedRunVersion =
    versions.data?.find(
      (version) => version.id === selectedRun?.taskPlanVersionId
    ) ??
    selectedVersion ??
    null;
  const units = useExecutionUnitsQuery(selectedRun?.id ?? null);
  const unitSummary = summarizeExecutionUnits(units.data?.items ?? []);
  const command = useTaskRunCommandMutation(projectId);
  const canOperate =
    session.data?.roles.some((role) => RUN_OPERATORS.has(role)) ?? false;
  const view = searchParams.get("view") === "list" ? "list" : "orbit";

  if (plans.isPending || runs.isPending) {
    return <LoadingState label="正在读取任务轨道" />;
  }
  if (plans.isError || runs.isError) {
    const failedQuery = plans.isError ? plans : runs;
    return (
      <ErrorState
        detail={failedQuery.error?.message ?? "无法读取任务轨道。"}
        onRetry={() => void failedQuery.refetch()}
      />
    );
  }

  const selectedPlan =
    plans.data.find((plan) => plan.id === selectedPlanId) ??
    plans.data[0] ??
    null;

  if (selectedPlan && versions.isPending) {
    return <LoadingState label="正在读取 TaskPlanVersion" />;
  }
  if (versions.isError) {
    return (
      <ErrorState
        detail={versions.error.message}
        onRetry={() => void versions.refetch()}
      />
    );
  }

  const versionById = new Map(
    (versions.data ?? []).map((version) => [version.id, version])
  );
  const activeCount = planRuns.filter((run) => run.lifecycle !== "CLOSED").length;
  const attentionCount = planRuns.filter((run) =>
    ["FAILED", "BLOCKED", "INFRA_ERROR"].includes(run.quality)
  ).length;
  const closedCount = planRuns.filter((run) => run.lifecycle === "CLOSED").length;
  const unitWindowIsComplete =
    Boolean(selectedRun) &&
    !units.data?.nextAfterOrdinal &&
    (selectedRun?.unitCount ?? unitSummary.total) <= unitSummary.total;
  const commandError =
    command.error instanceof ApiProblemError
      ? command.error.problem.detail
      : command.error?.message;

  async function submitCommand(kind: "cancel" | "pause" | "resume") {
    if (!selectedRun) return;
    await command.mutateAsync({
      runId: selectedRun.id,
      revision: selectedRun.revision,
      kind,
      command: {
        clientMutationId: `${kind}-run-${createRequestId()}`
      }
    });
  }

  return (
    <div className={styles.page}>
      <header className={styles.hero}>
        <div>
          <p>
            <Rocket size={13} /> MISSION CONTROL
          </p>
          <h1>让每一次回归，都沿着自己的轨道运行。</h1>
          <span>
            TaskPlan 固定任务身份，TaskPlanVersion 冻结执行输入，TaskRun
            记录每一次真实批量执行。
          </span>
        </div>
        <div className={styles.heroActions}>
          <button
            type="button"
            onClick={() => setCreatePlanOpen(true)}
            disabled={!canOperate}
            title={canOperate ? "创建真实 TaskPlan" : "需要运行操作权限"}
          >
            <Plus size={16} /> 创建 TaskPlan
          </button>
          <button
            type="button"
            onClick={() => setStartRunOpen(true)}
            disabled={!canOperate || !selectedVersion}
            title={
              selectedVersion
                ? "从不可变 TaskPlanVersion 启动"
                : "当前 TaskPlan 尚无已发布版本"
            }
          >
            <Play size={16} /> 创建批量任务
          </button>
        </div>
      </header>

      {!selectedPlan ? (
        <EmptyState
          title="还没有 TaskPlan"
          detail="由 RUN_OPERATOR 或项目管理员创建第一个稳定计划；后续版本发布与运行均保留完整审计链。"
        />
      ) : (
        <>
          <nav className={styles.planStrip} aria-label="TaskPlan">
            <span>TEST PLANS</span>
            {plans.data.map((plan) => (
              <Link
                href={searchHref(
                  pathname,
                  new URLSearchParams(searchParams.toString()),
                  {
                    planId: plan.id,
                    versionId: null,
                    runId: null
                  }
                )}
                aria-current={plan.id === selectedPlan.id ? "page" : undefined}
                key={plan.id}
              >
                <strong>{plan.name}</strong>
                <small>{plan.key}</small>
              </Link>
            ))}
          </nav>

          <section className={styles.commandBar}>
            <form>
              <Search size={15} aria-hidden="true" />
              <input type="hidden" name="planId" value={selectedPlan.id} />
              <input
                name="q"
                defaultValue={searchParams.get("q") ?? ""}
                aria-label="搜索任务"
                placeholder="搜索任务、状态或触发来源"
              />
            </form>
            <div className={styles.filters}>
              <span>全部 {planRuns.length}</span>
              <span>运行中 {activeCount}</span>
              <span>需关注 {attentionCount}</span>
              <span>已关闭 {closedCount}</span>
            </div>
            <div className={styles.viewSwitch}>
              <Link
                className={view === "orbit" ? styles.activeView : ""}
                href={searchHref(
                  pathname,
                  new URLSearchParams(searchParams.toString()),
                  { view: "orbit" }
                )}
              >
                <Globe2 size={14} /> 轨道
              </Link>
              <Link
                className={view === "list" ? styles.activeView : ""}
                href={searchHref(
                  pathname,
                  new URLSearchParams(searchParams.toString()),
                  { view: "list" }
                )}
              >
                <ListChecks size={14} /> 列表
              </Link>
            </div>
          </section>

          <section className={styles.centerStage}>
            <div className={styles.orbitBoard}>
              <header>
                <div>
                  <span>ACTIVE TASK ORBIT</span>
                  <strong>{view === "orbit" ? "任务运行轨道" : "任务运行磁带"}</strong>
                </div>
                <em>
                  <Radio size={11} /> API FACTS
                </em>
              </header>

              {!visibleRuns.length ? (
                <EmptyState
                  title={query ? "没有匹配的 TaskRun" : "这个计划还没有运行记录"}
                  detail={
                    versions.data?.length
                      ? "从已发布版本启动第一条真实 TaskRun。"
                      : "后端尚未为此 TaskPlan 发布不可变版本。"
                  }
                />
              ) : view === "orbit" && selectedRun ? (
                <div className={styles.orbitMap}>
                  <i className={styles.ringOne} />
                  <i className={styles.ringTwo} />
                  <i className={styles.ringThree} />
                  <Link
                    className={styles.orbitCore}
                    href={`/projects/${projectId}/live?runId=${selectedRun.id}`}
                    style={
                      {
                        "--progress": unitWindowIsComplete
                          ? `${unitSummary.progress}%`
                          : "0%"
                      } as ProgressStyle
                    }
                  >
                    <span>
                      {selectedRun.lifecycle} · RUN-{shortId(selectedRun.id)}
                    </span>
                    <strong>
                      {units.isPending
                        ? "…"
                        : unitWindowIsComplete
                          ? `${unitSummary.progress}%`
                          : "LIVE"}
                    </strong>
                    <small>
                      {units.isPending
                        ? "READING EXECUTION UNITS"
                        : unitWindowIsComplete
                          ? `${unitSummary.closed} / ${unitSummary.total} EXECUTIONS`
                          : `FIRST PAGE ${unitSummary.closed} / ${unitSummary.total} CLOSED`}
                    </small>
                    <i />
                  </Link>
                  {visibleRuns
                    .filter((run) => run.id !== selectedRun.id)
                    .slice(0, 3)
                    .map((run, index) => (
                      <Link
                        className={styles.satellite}
                        data-position={index + 1}
                        data-tone={statusTone(run)}
                        href={searchHref(
                          pathname,
                          new URLSearchParams(searchParams.toString()),
                          { runId: run.id }
                        )}
                        key={run.id}
                      >
                        <span>RUN-{shortId(run.id)}</span>
                        <strong>
                          {versionById.get(run.taskPlanVersionId)?.version ??
                            shortId(run.taskPlanVersionId)}
                        </strong>
                        <small>
                          {run.lifecycle} · {run.quality}
                        </small>
                        <i />
                      </Link>
                    ))}
                  <p className={styles.orbitCaption}>
                    <Bot size={15} /> ExecutionUnit 生命周期每 5 秒与控制面同步
                  </p>
                </div>
              ) : (
                <div className={styles.runList}>
                  {visibleRuns.map((run) => (
                    <RunCard
                      run={run}
                      version={versionById.get(run.taskPlanVersionId) ?? null}
                      selected={run.id === selectedRun?.id}
                      href={searchHref(
                        pathname,
                        new URLSearchParams(searchParams.toString()),
                        { runId: run.id }
                      )}
                      key={run.id}
                    />
                  ))}
                </div>
              )}
            </div>

            <aside className={styles.focus}>
              {selectedRun ? (
                <>
                  <header>
                    <span>TASK FOCUS</span>
                    <em data-tone={statusTone(selectedRun)}>
                      {selectedRun.lifecycle}
                    </em>
                  </header>
                  <h2>{selectedPlan.name}</h2>
                  <p>
                    {selectedRunVersion?.versionRef ??
                      `TaskPlanVersion ${shortId(selectedRun.taskPlanVersionId)}`}
                  </p>
                  <div className={styles.focusProgress}>
                    <div>
                      <strong>{units.isPending ? "…" : unitSummary.closed}</strong>
                      <span>
                        / {unitSummary.total} 当前页已关闭
                      </span>
                    </div>
                    <small>{selectedRun.materializationState}</small>
                    <i>
                      <b style={{ width: `${unitSummary.progress}%` }} />
                    </i>
                  </div>
                  <div className={styles.metrics}>
                    <div>
                      <span>通过</span>
                      <strong>{unitSummary.passed}</strong>
                    </div>
                    <div>
                      <span>失败</span>
                      <strong data-risk>{unitSummary.failed}</strong>
                    </div>
                    <div>
                      <span>环境异常</span>
                      <strong>{unitSummary.infraError}</strong>
                    </div>
                    <div>
                      <span>运行 / 排队</span>
                      <strong>
                        {unitSummary.running} / {unitSummary.queued}
                      </strong>
                    </div>
                  </div>
                  <div className={styles.snapshot}>
                    <span>冻结快照</span>
                    <small>
                      <CircleCheck size={11} />{" "}
                      {selectedRunVersion?.caseCount ?? "—"} CaseVersion
                    </small>
                    <small>
                      <CircleCheck size={11} />{" "}
                      {selectedRunVersion?.matrixSize ?? "—"} Matrix Units
                    </small>
                    <small>
                      <CircleCheck size={11} /> Manifest{" "}
                      {shortId(selectedRun.taskPlanVersionId)}
                    </small>
                  </div>
                  {commandError ? (
                    <p className={styles.inlineError} role="alert">
                      {commandError}
                    </p>
                  ) : null}
                  <div className={styles.controls}>
                    {selectedRun.lifecycle === "RUNNING" ? (
                      <button
                        type="button"
                        onClick={() => void submitCommand("pause")}
                        disabled={!canOperate || command.isPending}
                      >
                        <Pause size={14} /> 暂停派发
                      </button>
                    ) : selectedRun.lifecycle === "PAUSED" ? (
                      <button
                        type="button"
                        onClick={() => void submitCommand("resume")}
                        disabled={!canOperate || command.isPending}
                      >
                        <Play size={14} /> 继续派发
                      </button>
                    ) : null}
                    <button
                      type="button"
                      onClick={() => void submitCommand("cancel")}
                      disabled={
                        !canOperate ||
                        command.isPending ||
                        ["CLOSED", "CANCELING"].includes(selectedRun.lifecycle)
                      }
                    >
                      <CircleStop size={14} /> 取消
                    </button>
                  </div>
                  <Link
                    className={styles.focusAction}
                    href={`/projects/${projectId}/live?runId=${selectedRun.id}`}
                  >
                    进入批量现场 <ArrowRight size={15} />
                  </Link>
                </>
              ) : (
                <EmptyState
                  title="等待 TaskRun"
                  detail="选择已有运行，或从已发布版本创建新的批量任务。"
                />
              )}
            </aside>

            <div className={styles.signalStrip}>
              <div>
                <Rocket size={17} />
                <span>计划版本</span>
                <strong>{versions.data?.length ?? 0} 个不可变版本</strong>
                <small>{selectedVersion?.versionRef ?? "等待首次发布"}</small>
              </div>
              <div>
                <CircleCheck size={17} />
                <span>运行事实</span>
                <strong>{planRuns.length} 次真实运行</strong>
                <small>{activeCount} 个仍未关闭</small>
              </div>
              <div>
                <CircleAlert size={17} />
                <span>质量脉冲</span>
                <strong>{attentionCount} 个需关注结果</strong>
                <small>仅统计后端 Quality Axis</small>
              </div>
            </div>
          </section>

          <section className={styles.versionRail}>
            <header>
              <span>VERSION VAULT</span>
              <strong>{versions.data?.length ?? 0}</strong>
            </header>
            {versions.data?.length ? (
              versions.data.slice(0, 4).map((version) => (
                <Link
                  className={
                    version.id === selectedVersion?.id
                      ? styles.selectedVersion
                      : ""
                  }
                  href={searchHref(
                    pathname,
                    new URLSearchParams(searchParams.toString()),
                    { versionId: version.id }
                  )}
                  key={version.id}
                >
                  <span>{version.version}</span>
                  <strong>{version.versionRef}</strong>
                  <small>{version.caseCount} CASES</small>
                  <b>{version.matrixSize} UNITS</b>
                  <ArrowUpRight size={14} />
                </Link>
              ))
            ) : (
              <div className={styles.versionGap}>
                <strong>尚无已发布版本</strong>
                <span>
                  当前 API 未开放 Execution/Profile Catalog，前端不会要求操作者手填无来源 UUID。
                </span>
                <button
                  type="button"
                  disabled
                  title="等待后端开放 Profile Catalog 后接入完整版本发布向导"
                >
                  发布 TaskPlanVersion
                </button>
              </div>
            )}
          </section>
        </>
      )}

      <CreateTaskPlanDialog
        projectId={projectId}
        open={createPlanOpen}
        onClose={() => setCreatePlanOpen(false)}
      />
      <StartTaskRunDialog
        projectId={projectId}
        version={selectedVersion}
        open={startRunOpen}
        onClose={() => setStartRunOpen(false)}
      />
    </div>
  );
}
