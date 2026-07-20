"use client";

import {
  ArrowLeft,
  ArrowRight,
  ArrowUpRight,
  Bot,
  CircleAlert,
  CircleCheck,
  CircleStop,
  Clock3,
  Fingerprint,
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
  useRouter,
  useSearchParams
} from "next/navigation";
import {
  useMemo,
  useState,
  type CSSProperties
} from "react";

import { useSessionQuery } from "@/features/auth/api/auth-queries";
import { useIdentityWalletQuery } from "@/features/identity/api/identity-queries";
import { ApiProblemError } from "@/shared/api/problem";
import { createRequestId } from "@/shared/api/request-id";
import { ErrorState } from "@/shared/ui/feedback/error-state";
import { LoadingState } from "@/shared/ui/feedback/loading-state";

import {
  useExecutionUnitsQuery,
  useTaskAssemblyCatalogQuery,
  useTaskControlCatalogQuery,
  useTaskRunCommandMutation,
  useTaskRunsQuery
} from "../api/task-queries";
import { summarizeExecutionUnits } from "../model/task-mapper";
import type {
  TaskPlanVersionViewModel,
  TaskRunViewModel
} from "../model/task";
import { CreateTaskPlanDialog } from "./create-task-plan-dialog";
import { TaskBuilder } from "./task-builder";
import styles from "./task-page.module.css";

const RUN_OPERATORS = new Set([
  "ORG_ADMIN",
  "PROJECT_ADMIN",
  "RUN_OPERATOR"
]);

type ProgressStyle = CSSProperties & { "--progress": string };
type RunFilter = "all" | "active" | "attention" | "waiting";

const DATE_FORMAT = new Intl.DateTimeFormat("zh-CN", {
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit"
});

const FIRE_DATE_FORMAT = new Intl.DateTimeFormat("zh-CN", {
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  timeZoneName: "short"
});

function shortId(value: string): string {
  return value.slice(0, 8).toUpperCase();
}

function statusTone(run: TaskRunViewModel): "good" | "risk" | "active" | "muted" {
  if (["QUEUED", "MATERIALIZING"].includes(run.lifecycle)) return "muted";
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
  const query = next.toString();
  return query ? `${pathname}?${query}` : pathname;
}

function matchesFilter(run: TaskRunViewModel, filter: RunFilter): boolean {
  if (filter === "active") return run.lifecycle !== "CLOSED";
  if (filter === "attention") {
    return ["FAILED", "BLOCKED", "INFRA_ERROR"].includes(run.quality);
  }
  if (filter === "waiting") {
    return (
      run.lifecycle === "QUEUED" ||
      run.materializationState === "MATERIALIZING"
    );
  }
  return true;
}

function RunCard({
  run,
  planName,
  version,
  href,
  selected
}: Readonly<{
  run: TaskRunViewModel;
  planName: string;
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
        <strong>{planName}</strong>
      </div>
      <b>{run.lifecycle}</b>
      <small>{run.quality}</small>
      <em>{version?.version ?? DATE_FORMAT.format(run.requestedAt)}</em>
      <ArrowUpRight size={15} aria-hidden="true" />
    </Link>
  );
}

export function TaskPage({ projectId }: Readonly<{ projectId: string }>) {
  const pathname = usePathname();
  const router = useRouter();
  const searchParams = useSearchParams();
  const session = useSessionQuery();
  const identity = useIdentityWalletQuery(projectId);
  const control = useTaskControlCatalogQuery(projectId);
  const runs = useTaskRunsQuery(projectId);
  const builderOpen = searchParams.get("panel") === "create";
  const assembly = useTaskAssemblyCatalogQuery(projectId, builderOpen);
  const [createPlanOpen, setCreatePlanOpen] = useState(false);
  const canOperate =
    session.data?.roles.some((role) => RUN_OPERATORS.has(role)) ?? false;

  const versionById = useMemo(
    () =>
      new Map(
        (control.data?.versions ?? []).map((version) => [version.id, version])
      ),
    [control.data?.versions]
  );
  const planById = useMemo(
    () =>
      new Map((control.data?.plans ?? []).map((plan) => [plan.id, plan])),
    [control.data?.plans]
  );
  const query = searchParams.get("q")?.trim().toLowerCase() ?? "";
  const requestedFilter = searchParams.get("filter");
  const filter: RunFilter = ["active", "attention", "waiting"].includes(
    requestedFilter ?? ""
  )
    ? (requestedFilter as RunFilter)
    : "all";
  const view = searchParams.get("view") === "list" ? "list" : "orbit";
  const filteredRuns = useMemo(() => {
    const candidates =
      runs.data?.filter((run) => matchesFilter(run, filter)) ?? [];
    if (!query) return candidates;
    return candidates.filter((run) => {
      const version = versionById.get(run.taskPlanVersionId);
      const plan = version ? planById.get(version.taskPlanId) : null;
      return [
        run.id,
        run.lifecycle,
        run.quality,
        run.triggerSource,
        version?.versionRef ?? "",
        plan?.name ?? "",
        plan?.key ?? ""
      ].some((value) => value.toLowerCase().includes(query));
    });
  }, [filter, planById, query, runs.data, versionById]);

  const requestedRunId = searchParams.get("runId");
  const selectedRun =
    filteredRuns.find((run) => run.id === requestedRunId) ??
    filteredRuns[0] ??
    null;
  const selectedVersion = selectedRun
    ? versionById.get(selectedRun.taskPlanVersionId) ?? null
    : null;
  const selectedPlan = selectedVersion
    ? planById.get(selectedVersion.taskPlanId) ?? null
    : null;
  const units = useExecutionUnitsQuery(selectedRun?.id ?? null);
  const unitSummary = summarizeExecutionUnits(units.data?.items ?? []);
  const denominator = selectedRun?.unitCount ?? unitSummary.total;
  const progress =
    denominator > 0
      ? Math.min(100, Math.round((unitSummary.closed / denominator) * 100))
      : 0;
  const unitWindowIsComplete =
    Boolean(selectedRun) &&
    !units.data?.nextAfterOrdinal &&
    denominator <= unitSummary.total;
  const command = useTaskRunCommandMutation(projectId);

  if (control.isPending || runs.isPending) {
    return <LoadingState label="正在读取任务指挥舱" />;
  }
  if (control.isError || runs.isError) {
    const failedQuery = control.isError ? control : runs;
    return (
      <ErrorState
        detail={failedQuery.error?.message ?? "无法读取任务指挥舱。"}
        onRetry={() => void failedQuery.refetch()}
      />
    );
  }
  if (builderOpen && assembly.isPending) {
    return <LoadingState label="正在读取批量任务装配目录" />;
  }
  if (builderOpen && assembly.isError) {
    return (
      <ErrorState
        detail={assembly.error.message}
        onRetry={() => void assembly.refetch()}
      />
    );
  }

  const allRuns = runs.data;
  const activeCount = allRuns.filter((run) => run.lifecycle !== "CLOSED").length;
  const attentionCount = allRuns.filter((run) =>
    ["FAILED", "BLOCKED", "INFRA_ERROR"].includes(run.quality)
  ).length;
  const waitingCount = allRuns.filter(
    (run) =>
      run.lifecycle === "QUEUED" ||
      run.materializationState === "MATERIALIZING"
  ).length;
  const nextSchedule = control.data.schedules.find(
    (schedule) =>
      schedule.status === "ACTIVE" && schedule.nextFireTimes.length > 0
  );
  const nextFire = nextSchedule?.nextFireTimes[0] ?? null;
  const schedulerHealthy = control.data.schedules.every(
    (schedule) => schedule.syncStatus === "SYNCED"
  );
  const identityTotal = identity.data
    ? identity.data.totals.available +
      identity.data.totals.leased +
      identity.data.totals.quarantined
    : null;
  const commandError =
    command.error instanceof ApiProblemError
      ? command.error.problem.detail
      : command.error?.message;

  async function submitCommand(kind: "cancel" | "pause" | "resume") {
    if (!selectedRun) return;
    try {
      await command.mutateAsync({
        runId: selectedRun.id,
        revision: selectedRun.revision,
        kind,
        command: {
          clientMutationId: `${kind}-run-${createRequestId()}`
        }
      });
    } catch {
      // Mutation state renders the backend problem.
    }
  }

  return (
    <div className={styles.page}>
      <header className={styles.hero}>
        <div>
          <p>
            <Rocket size={13} />{" "}
            {builderOpen
              ? "TASK ASSEMBLY · NEW MISSION"
              : "MISSION CONTROL"}
          </p>
          <h1>
            {builderOpen
              ? "把测试范围，展开成一张真实执行矩阵。"
              : "让每一次回归，都沿着自己的轨道运行。"}
          </h1>
          <span>
            {builderOpen
              ? "每一次选择都会立即反映为精确 CaseVersion、环境与执行单元；未开放的 Profile Catalog 不会被演示数据替代。"
              : "任务记录一次真实批量执行；在这里观察进度、资源、失败聚集与下一次触发。"}
          </span>
        </div>
        <Link
          className={styles.heroAction}
          href={searchHref(
            pathname,
            new URLSearchParams(searchParams.toString()),
            builderOpen
              ? { panel: null }
              : { panel: "create", runId: null }
          )}
        >
          {builderOpen ? (
            <>
              <ArrowLeft size={15} /> 返回任务中心
            </>
          ) : (
            <>
              <Plus size={15} /> 创建批量任务
            </>
          )}
        </Link>
      </header>

      {builderOpen && assembly.data ? (
        <TaskBuilder
          projectId={projectId}
          control={control.data}
          assembly={assembly.data}
          identity={identity.data ?? null}
          canOperate={canOperate}
          onBack={() =>
            router.push(
              searchHref(
                pathname,
                new URLSearchParams(searchParams.toString()),
                { panel: null }
              )
            )
          }
          onCreatePlan={() => setCreatePlanOpen(true)}
        />
      ) : (
        <>
          <section className={styles.commandBar}>
            <form>
              <Search size={15} aria-hidden="true" />
              <input
                name="q"
                defaultValue={searchParams.get("q") ?? ""}
                aria-label="搜索任务"
                placeholder="搜索任务、迭代或触发来源"
              />
              {filter !== "all" ? (
                <input type="hidden" name="filter" value={filter} />
              ) : null}
              {view !== "orbit" ? (
                <input type="hidden" name="view" value={view} />
              ) : null}
            </form>
            <div className={styles.filters}>
              {[
                ["all", `全部 ${allRuns.length}`],
                ["active", `运行中 ${activeCount}`],
                ["attention", `需关注 ${attentionCount}`],
                ["waiting", `等待资源 ${waitingCount}`]
              ].map(([value, label]) => (
                <Link
                  className={filter === value ? styles.activeFilter : ""}
                  href={searchHref(
                    pathname,
                    new URLSearchParams(searchParams.toString()),
                    {
                      filter: value === "all" ? null : value,
                      runId: null
                    }
                  )}
                  key={value}
                >
                  {label}
                </Link>
              ))}
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
                <em data-healthy={schedulerHealthy}>
                  <Radio size={11} />{" "}
                  {control.data.schedules.length
                    ? schedulerHealthy
                      ? "调度器在线"
                      : "调度同步中"
                    : "API FACTS"}
                </em>
              </header>

              {view === "orbit" ? (
                <div className={styles.orbitMap}>
                  <i className={styles.ringOne} />
                  <i className={styles.ringTwo} />
                  <i className={styles.ringThree} />
                  {selectedRun ? (
                    <Link
                      className={styles.orbitCore}
                      href={`/projects/${projectId}/live?runId=${selectedRun.id}`}
                      style={
                        {
                          "--progress": `${progress}%`
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
                            ? `${progress}%`
                            : "LIVE"}
                      </strong>
                      <small>
                        {units.isPending
                          ? "READING EXECUTION UNITS"
                          : `${unitSummary.closed} / ${denominator} EXECUTIONS`}
                      </small>
                      <i />
                    </Link>
                  ) : (
                    <div
                      className={`${styles.orbitCore} ${styles.emptyOrbitCore}`}
                      style={{ "--progress": "0%" } as ProgressStyle}
                    >
                      <span>READY · TASK CONTROL</span>
                      <strong>—</strong>
                      <small>等待第一条 TaskRun</small>
                      <i />
                    </div>
                  )}
                  {filteredRuns
                    .filter((run) => run.id !== selectedRun?.id)
                    .slice(0, 3)
                    .map((run, index) => {
                      const version = versionById.get(run.taskPlanVersionId);
                      const plan = version
                        ? planById.get(version.taskPlanId)
                        : null;
                      return (
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
                          <strong>{plan?.name ?? "未解析 TaskPlan"}</strong>
                          <small>
                            {run.lifecycle} · {run.quality}
                          </small>
                          <i />
                        </Link>
                      );
                    })}
                  <p className={styles.orbitCaption}>
                    <Bot size={15} />
                    {selectedRun
                      ? `ExecutionUnit 每 5 秒与控制面同步`
                      : "创建或触发 TaskPlanVersion 后，运行会出现在这里"}
                  </p>
                </div>
              ) : (
                <div className={styles.runList}>
                  {filteredRuns.length ? (
                    filteredRuns.map((run) => {
                      const version =
                        versionById.get(run.taskPlanVersionId) ?? null;
                      const plan = version
                        ? planById.get(version.taskPlanId)
                        : null;
                      return (
                        <RunCard
                          run={run}
                          planName={plan?.name ?? "未解析 TaskPlan"}
                          version={version}
                          selected={run.id === selectedRun?.id}
                          href={searchHref(
                            pathname,
                            new URLSearchParams(searchParams.toString()),
                            { runId: run.id }
                          )}
                          key={run.id}
                        />
                      );
                    })
                  ) : (
                    <div className={styles.emptyList}>
                      <strong>
                        {query || filter !== "all"
                          ? "没有匹配的 TaskRun"
                          : "还没有 TaskRun"}
                      </strong>
                      <span>可从已发布 TaskPlanVersion 手工或定时触发。</span>
                    </div>
                  )}
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
                  <h2>{selectedPlan?.name ?? "未解析 TaskPlan"}</h2>
                  <p>
                    {selectedVersion?.versionRef ??
                      `TaskPlanVersion ${shortId(selectedRun.taskPlanVersionId)}`}
                  </p>
                  <div className={styles.focusProgress}>
                    <div>
                      <strong>{units.isPending ? "…" : unitSummary.closed}</strong>
                      <span>/ {denominator} 已完成</span>
                    </div>
                    <small>{selectedRun.materializationState}</small>
                    <i>
                      <b style={{ width: `${progress}%` }} />
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
                      {selectedVersion?.caseCount ?? "—"} CaseVersion
                    </small>
                    <small>
                      <CircleCheck size={11} />{" "}
                      {selectedVersion?.matrixSize ?? "—"} Matrix Units
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
                <div className={styles.emptyFocus}>
                  <Radio size={22} />
                  <span>TASK FOCUS</span>
                  <h2>等待 TaskRun</h2>
                  <p>
                    创建或选择一个已发布 TaskPlanVersion，运行事实会在这里聚焦。
                  </p>
                  <Link
                    href={searchHref(
                      pathname,
                      new URLSearchParams(searchParams.toString()),
                      { panel: "create" }
                    )}
                  >
                    打开批量任务装配器 <ArrowRight size={14} />
                  </Link>
                </div>
              )}
            </aside>

            <div className={styles.signalStrip}>
              <div>
                <Clock3 size={17} />
                <span>下一次计划</span>
                <strong>
                  {nextFire && nextSchedule
                    ? `${FIRE_DATE_FORMAT.format(nextFire)} · ${nextSchedule.name}`
                    : "暂无 ACTIVE Schedule"}
                </strong>
                <small>
                  {nextSchedule
                    ? `${nextSchedule.status} · ${nextSchedule.syncStatus}`
                    : `${control.data.versions.length} 个不可变版本`}
                </small>
              </div>
              <div>
                <Fingerprint size={17} />
                <span>身份容量</span>
                <strong>
                  {identity.data && identityTotal !== null
                    ? `${identity.data.totals.available} / ${identityTotal} 可用`
                    : identity.isError
                      ? "容量读取失败"
                      : "等待身份目录"}
                </strong>
                <small>
                  {identity.data?.environment?.name ?? "尚无 ACTIVE Environment"}
                </small>
              </div>
              <div>
                <CircleAlert size={17} />
                <span>风险脉冲</span>
                <strong>{attentionCount} 个结果需关注</strong>
                <small>
                  {waitingCount
                    ? `${waitingCount} 个运行等待资源或物化`
                    : "当前没有资源排队"}
                </small>
              </div>
            </div>
          </section>

          <section className={styles.historyRail}>
            <header>
              <span>最近任务</span>
              <strong>{allRuns.length}</strong>
            </header>
            {allRuns.length ? (
              allRuns.slice(0, 4).map((run) => {
                const version = versionById.get(run.taskPlanVersionId);
                const plan = version ? planById.get(version.taskPlanId) : null;
                return (
                  <Link
                    className={styles.historyTask}
                    data-tone={statusTone(run)}
                    href={searchHref(
                      pathname,
                      new URLSearchParams(searchParams.toString()),
                      { runId: run.id, filter: null }
                    )}
                    key={run.id}
                  >
                    <span>RUN-{shortId(run.id)}</span>
                    <strong>{plan?.name ?? "未解析 TaskPlan"}</strong>
                    <small>
                      {run.triggerSource} · {DATE_FORMAT.format(run.requestedAt)}
                    </small>
                    <b>
                      {run.lifecycle !== "CLOSED"
                        ? run.lifecycle
                        : run.quality}
                    </b>
                    <ArrowUpRight size={14} />
                  </Link>
                );
              })
            ) : (
              <div className={styles.historyGap}>
                <strong>尚无最近任务</strong>
                <span>第一次真实运行完成后会保留在 RECENT 轨道。</span>
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
    </div>
  );
}
