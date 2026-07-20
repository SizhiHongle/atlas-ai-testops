"use client";

import {
  ArrowLeft,
  ArrowRight,
  ArrowUpRight,
  Bot,
  Camera,
  Check,
  CircleAlert,
  CircleStop,
  Clock3,
  Eye,
  Fingerprint,
  Globe2,
  Layers3,
  MonitorDot,
  Network,
  Pause,
  Play,
  Radio,
  Rocket,
  ShieldCheck,
  Terminal
} from "lucide-react";
import Link from "next/link";
import {
  usePathname,
  useSearchParams
} from "next/navigation";
import {
  useMemo,
  useState
} from "react";

import { useSessionQuery } from "@/features/auth/api/auth-queries";
import {
  useExecutionUnitsQuery,
  useTaskAssemblyCatalogQuery,
  useTaskControlCatalogQuery,
  useTaskRunCommandMutation,
  useTaskRunsQuery
} from "@/features/task/api/task-queries";
import { summarizeExecutionUnits } from "@/features/task/model/task-mapper";
import type {
  ExecutionUnitViewModel,
  PublishedCaseVersionViewModel
} from "@/features/task/model/task";
import { ApiProblemError } from "@/shared/api/problem";
import { createRequestId } from "@/shared/api/request-id";
import { ErrorState } from "@/shared/ui/feedback/error-state";
import { LoadingState } from "@/shared/ui/feedback/loading-state";

import {
  useLiveControlMutation,
  useLiveSnapshotQuery,
  useUnitAttemptsQuery
} from "../api/live-queries";
import type { LiveControlKind } from "../model/live";
import styles from "./live-page.module.css";

const RUN_OPERATORS = new Set([
  "ORG_ADMIN",
  "PROJECT_ADMIN",
  "RUN_OPERATOR"
]);

const DATE_FORMAT = new Intl.DateTimeFormat("zh-CN", {
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit"
});

type MatrixDimension = "role" | "browser";

type ExecutionGroup = {
  key: string;
  label: string;
  units: ExecutionUnitViewModel[];
};

function shortId(value: string): string {
  return value.slice(0, 8).toUpperCase();
}

function pageHref(
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

function unitTone(lifecycle: string, quality: string): string {
  if (lifecycle === "RUNNING") return "running";
  if (lifecycle === "QUEUED") return "queued";
  if (quality === "PASSED") return "passed";
  if (quality === "INFRA_ERROR") return "infra";
  if (quality === "FAILED" || quality === "BLOCKED") return "failed";
  if (quality === "CANCELED") return "canceled";
  return "pending";
}

function problemMessage(error: unknown): string | null {
  if (error instanceof ApiProblemError) return error.problem.detail;
  return error instanceof Error ? error.message : null;
}

function caseLabel(
  caseVersion: PublishedCaseVersionViewModel | undefined,
  caseVersionId: string
): string {
  return caseVersion?.caseName ?? `CASE-${shortId(caseVersionId)}`;
}

function controlIntent(state: string | undefined): string {
  switch (state) {
    case "AGENT_CONTROLLED":
      return "Agent 持有当前控制租约；接管会先进入安全点并递增 epoch。";
    case "HUMAN_CONTROLLED":
      return "人工持有排他控制租约；交还前必须完成 reconcile。";
    case "QUIESCING":
      return "正在等待 in-flight action 收敛，不再签发新的 ActionGrant。";
    case "RECONCILING":
      return "正在重建 Page、DOM 与浏览器事实快照。";
    case "PAUSED":
      return "Attempt 已停在安全点，当前不会产生新的浏览器副作用。";
    case "NO_CONTROLLER":
      return "当前没有有效 controller；浏览器输入保持冻结。";
    default:
      return "LiveSession 尚未建立可验证的控制事实。";
  }
}

function EmptyBatchLive({ projectId }: Readonly<{ projectId: string }>) {
  return (
    <div className={styles.page}>
      <header className={styles.hero}>
        <div>
          <p>
            <MonitorDot size={13} /> TASK CONTROL
          </p>
          <h1>批量现场等待第一条真实运行。</h1>
          <span>
            TaskRun 启动后，ExecutionUnit 与 LiveSession 会在这里形成真实现场。
          </span>
        </div>
      </header>
      <section className={styles.emptyCockpit}>
        <div className={styles.emptyOrbit} aria-hidden="true">
          <i />
          <i />
          <span>
            <Radio size={22} />
          </span>
        </div>
        <div>
          <span>NO ACTIVE TASK RUN</span>
          <h2>先冻结一个 TaskPlanVersion，再进入同一片执行现场。</h2>
          <p>
            现场不会生成演示 Unit。Manual 或 Schedule 创建的真实 TaskRun
            会携带不可变 Manifest、ExecutionUnit 与首个 UnitAttempt。
          </p>
          <Link href={`/projects/${projectId}/tasks`}>
            前往任务中心 <ArrowRight size={14} />
          </Link>
        </div>
        <aside>
          <span>LIVE CONTRACT</span>
          <dl>
            <div>
              <dt>运行绑定</dt>
              <dd>TaskRun → Unit → Attempt</dd>
            </div>
            <div>
              <dt>控制门禁</dt>
              <dd>Epoch + Fencing</dd>
            </div>
            <div>
              <dt>观看边界</dt>
              <dd>无事实不造画面</dd>
            </div>
          </dl>
        </aside>
      </section>
    </div>
  );
}

export function BatchLiveConsole({
  projectId
}: Readonly<{ projectId: string }>) {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const session = useSessionQuery();
  const runs = useTaskRunsQuery(projectId);
  const requestedRunId = searchParams.get("runId");
  const selectedRun =
    runs.data?.find((run) => run.id === requestedRunId) ??
    runs.data?.find((run) => run.lifecycle !== "CLOSED") ??
    runs.data?.[0] ??
    null;
  const parsedAfter = Number(searchParams.get("after") ?? "0");
  const afterOrdinal =
    Number.isSafeInteger(parsedAfter) && parsedAfter >= 0 ? parsedAfter : 0;
  const units = useExecutionUnitsQuery(selectedRun?.id ?? null, afterOrdinal);
  const controlCatalog = useTaskControlCatalogQuery(projectId);
  const assemblyCatalog = useTaskAssemblyCatalogQuery(
    projectId,
    Boolean(selectedRun)
  );
  const unitItems = useMemo(() => units.data?.items ?? [], [units.data?.items]);
  const requestedUnitId = searchParams.get("unitId");
  const selectedUnit =
    unitItems.find((unit) => unit.id === requestedUnitId) ??
    unitItems.find((unit) => unit.lifecycle === "RUNNING") ??
    unitItems[0] ??
    null;
  const attempts = useUnitAttemptsQuery(
    selectedRun?.id ?? null,
    selectedUnit?.id ?? null
  );
  const requestedAttemptId = searchParams.get("attemptId");
  const selectedAttempt =
    attempts.data?.find((attempt) => attempt.id === requestedAttemptId) ??
    attempts.data?.[0] ??
    null;
  const snapshot = useLiveSnapshotQuery(selectedAttempt?.id ?? null);
  const liveControl = useLiveControlMutation(selectedAttempt?.id ?? null);
  const runControl = useTaskRunCommandMutation(projectId);
  const [cancelArmed, setCancelArmed] = useState(false);

  const canOperate =
    session.data?.roles.some((role) => RUN_OPERATORS.has(role)) ?? false;
  const summary = summarizeExecutionUnits(unitItems);
  const dimension: MatrixDimension =
    searchParams.get("group") === "browser" ? "browser" : "role";

  const caseVersionById = useMemo(
    () =>
      new Map(
        (assemblyCatalog.data?.caseVersions ?? []).map((version) => [
          version.id,
          version
        ])
      ),
    [assemblyCatalog.data?.caseVersions]
  );
  const selectedVersion = controlCatalog.data?.versions.find(
    (version) => version.id === selectedRun?.taskPlanVersionId
  );
  const selectedPlan = controlCatalog.data?.plans.find(
    (plan) => plan.id === selectedVersion?.taskPlanId
  );
  const manifestCount =
    selectedVersion?.caseCount ??
    new Set(unitItems.map((unit) => unit.caseVersionId)).size;
  const matrixLabel = selectedVersion
    ? `${selectedVersion.caseCount} CaseVersion × ${Math.max(
        1,
        selectedVersion.browserProfileVersionIds.length
      )} 浏览器`
    : `${manifestCount || "—"} CaseVersion · exact Unit window`;

  const groups = useMemo<ExecutionGroup[]>(() => {
    const grouped = new Map<string, ExecutionGroup>();
    unitItems.forEach((unit) => {
      const version = caseVersionById.get(unit.caseVersionId);
      const key =
        dimension === "browser"
          ? unit.browserProfileVersionId
          : version?.roleKey ?? unit.caseVersionId;
      const label =
        dimension === "browser"
          ? `Browser ${shortId(unit.browserProfileVersionId)}`
          : version?.roleKey ??
            caseLabel(version, unit.caseVersionId);
      const current = grouped.get(key);
      if (current) current.units.push(unit);
      else grouped.set(key, { key, label, units: [unit] });
    });
    return [...grouped.values()];
  }, [caseVersionById, dimension, unitItems]);

  const workerLanes = useMemo(() => {
    const byCase = new Map<string, ExecutionUnitViewModel[]>();
    unitItems.forEach((unit) => {
      const current = byCase.get(unit.caseVersionId);
      if (current) current.push(unit);
      else byCase.set(unit.caseVersionId, [unit]);
    });
    return [...byCase.entries()].slice(0, 8);
  }, [unitItems]);

  if (runs.isPending) return <LoadingState label="正在读取批量现场" />;
  if (runs.isError) {
    return (
      <ErrorState
        detail={runs.error.message}
        onRetry={() => void runs.refetch()}
      />
    );
  }
  if (!selectedRun) return <EmptyBatchLive projectId={projectId} />;

  const liveSession = snapshot.data?.session ?? null;
  const isChangingControl =
    liveControl.isPending || Boolean(snapshot.data?.pendingCommand);
  const errorMessage = problemMessage(
    units.error ??
      attempts.error ??
      snapshot.error ??
      liveControl.error ??
      runControl.error
  );
  const selectedCaseVersion = selectedUnit
    ? caseVersionById.get(selectedUnit.caseVersionId)
    : undefined;
  const pageStart = unitItems[0]?.ordinal ?? afterOrdinal + 1;
  const pageEnd = unitItems.at(-1)?.ordinal ?? afterOrdinal;
  const activeAttemptControl:
    | { kind: LiveControlKind; label: string; icon: "eye" | "bot" | "play" }
    | null =
    liveSession?.state === "AGENT_CONTROLLED"
      ? { kind: "takeover", label: "接管当前执行", icon: "eye" }
      : liveSession?.state === "HUMAN_CONTROLLED"
        ? { kind: "return", label: "交还 Agent", icon: "bot" }
        : liveSession?.state === "PAUSED"
          ? { kind: "resume", label: "恢复 Attempt", icon: "play" }
          : null;

  async function submitLiveControl(kind: LiveControlKind) {
    if (!liveSession) return;
    try {
      await liveControl.mutateAsync({
        controlEpoch: liveSession.controlEpoch,
        kind,
        command: {
          reason: `Requested from Atlas live console: ${kind}.`,
          requestedTtlSec: kind === "takeover" ? 300 : null
        }
      });
    } catch {
      // Mutation state renders the authoritative Problem Details.
    }
  }

  async function submitRunControl(kind: "pause" | "resume" | "cancel") {
    if (!selectedRun) return;
    try {
      await runControl.mutateAsync({
        runId: selectedRun.id,
        revision: selectedRun.revision,
        kind,
        command: {
          clientMutationId: `${kind}-run-${createRequestId()}`
        }
      });
      setCancelArmed(false);
    } catch {
      // Mutation state renders the authoritative Problem Details.
    }
  }

  function requestCancel() {
    if (!cancelArmed) {
      setCancelArmed(true);
      return;
    }
    void submitRunControl("cancel");
  }

  return (
    <div className={styles.page}>
      <header className={styles.hero}>
        <div>
          <p>
            <MonitorDot size={13} /> TASK CONTROL · RUN-
            {shortId(selectedRun.id)}
          </p>
          <h1>
            {selectedRun.unitCount ?? "—"} 条执行，正在同一片现场发生。
          </h1>
          <span>
            从 TaskRun 全局观察资源与失败，再进入 exact UnitAttempt
            检查控制权与浏览器事实。
          </span>
        </div>
        <em data-state={selectedRun.lifecycle}>
          <Radio size={11} /> {selectedRun.lifecycle}
        </em>
      </header>

      <section className={styles.contextBanner}>
        <div>
          <Rocket size={18} />
          <span>TASK RUN</span>
          <strong>
            {manifestCount || "—"} 个 CaseVersion Manifest
          </strong>
        </div>
        <p>
          {selectedPlan?.name ?? "TaskPlanVersion"} ·{" "}
          {selectedVersion?.versionRef ??
            shortId(selectedRun.taskPlanVersionId)}
          ；后续草稿和新版本不会改变本次结果。
        </p>
        <Link href={`/projects/${projectId}/results?runId=${selectedRun.id}`}>
          查看正式结果 <ArrowRight size={13} />
        </Link>
      </section>

      <section className={styles.liveRibbon}>
        <div className={styles.progressCore}>
          <i
            style={
              {
                "--live-progress": `${summary.progress}%`
              } as React.CSSProperties
            }
          />
          <div>
            <span>WINDOW</span>
            <strong>{units.isPending ? "…" : `${summary.progress}%`}</strong>
          </div>
        </div>
        <div>
          <span>当前窗口完成</span>
          <strong>
            {summary.closed} / {summary.total || "—"}
          </strong>
          <small>
            Unit {pageStart}—{pageEnd || "—"}
          </small>
        </div>
        <div>
          <span>稳定通过</span>
          <strong>{summary.passed}</strong>
          <small>Quality = PASSED</small>
        </div>
        <div>
          <span>失败 / 阻塞</span>
          <strong data-risk>
            {summary.failed} / {summary.blocked}
          </strong>
          <small>不与生命周期混用</small>
        </div>
        <div>
          <span>环境异常</span>
          <strong>{summary.infraError}</strong>
          <small>Quality = INFRA_ERROR</small>
        </div>
        <div>
          <span>运行 / 排队</span>
          <strong>
            {summary.running} / {summary.queued}
          </strong>
          <small>{selectedRun.materializationState}</small>
        </div>
      </section>

      {units.isPending ? (
        <LoadingState label="正在读取 ExecutionUnit" />
      ) : units.isError ? (
        <ErrorState
          detail={units.error.message}
          onRetry={() => void units.refetch()}
        />
      ) : !unitItems.length ? (
        <section className={styles.materializingState}>
          <Layers3 size={25} />
          <span>EXECUTION MATRIX</span>
          <h2>
            {selectedRun.materializationState === "MATERIALIZING"
              ? "TaskRun 正在可恢复物化。"
              : "这个窗口还没有 ExecutionUnit。"}
          </h2>
          <p>
            {selectedRun.materializationState === "MATERIALIZING"
              ? "页面会读取数据库权威的下一批 Unit，不预先生成演示格子。"
              : "请返回任务中心检查冻结 Manifest 或选择其他 TaskRun。"}
          </p>
          <Link href={`/projects/${projectId}/tasks?runId=${selectedRun.id}`}>
            返回任务中心 <ArrowRight size={14} />
          </Link>
        </section>
      ) : (
        <section className={styles.console}>
          <aside className={styles.workerRail}>
            <header>
              <span>WORKER LANES</span>
              <b>{String(workerLanes.length).padStart(2, "0")}</b>
            </header>
            <div>
              {workerLanes.map(([caseVersionId, laneUnits], index) => {
                const version = caseVersionById.get(caseVersionId);
                const focused =
                  selectedUnit?.caseVersionId === caseVersionId;
                return (
                  <Link
                    data-active={focused}
                    href={pageHref(
                      pathname,
                      new URLSearchParams(searchParams.toString()),
                      {
                        unitId: laneUnits[0]?.id ?? null,
                        attemptId: null
                      }
                    )}
                    key={caseVersionId}
                  >
                    <span>W{String(index + 1).padStart(2, "0")}</span>
                    <div>
                      <strong>{caseLabel(version, caseVersionId)}</strong>
                      <small>
                        {version
                          ? `${version.version} · ${version.roleKey ?? "role locked"}`
                          : `${laneUnits.length} exact Units`}
                      </small>
                    </div>
                    <i
                      data-tone={
                        laneUnits.some((unit) => unit.lifecycle === "RUNNING")
                          ? "running"
                          : laneUnits.some((unit) =>
                              ["FAILED", "BLOCKED"].includes(unit.quality)
                            )
                            ? "failed"
                            : "passed"
                      }
                    />
                  </Link>
                );
              })}
            </div>
            <footer>
              <span>窗口容量</span>
              <div>
                <i
                  style={{
                    width: `${Math.min(100, unitItems.length)}%`
                  }}
                />
              </div>
              <strong>{unitItems.length} / 100</strong>
            </footer>
          </aside>

          <div className={styles.matrixPanel}>
            <header>
              <div>
                <span>EXECUTION MATRIX</span>
                <strong>{matrixLabel}</strong>
              </div>
              <nav aria-label="矩阵分组与分页">
                <Link
                  data-active={dimension === "role"}
                  href={pageHref(
                    pathname,
                    new URLSearchParams(searchParams.toString()),
                    { group: "role" }
                  )}
                >
                  按角色
                </Link>
                <Link
                  data-active={dimension === "browser"}
                  href={pageHref(
                    pathname,
                    new URLSearchParams(searchParams.toString()),
                    { group: "browser" }
                  )}
                >
                  按浏览器
                </Link>
                {afterOrdinal > 0 ? (
                  <Link
                    aria-label="上一页 ExecutionUnit"
                    href={pageHref(
                      pathname,
                      new URLSearchParams(searchParams.toString()),
                      {
                        after: String(Math.max(0, afterOrdinal - 100)),
                        unitId: null,
                        attemptId: null
                      }
                    )}
                  >
                    <ArrowLeft size={12} />
                  </Link>
                ) : null}
                {units.data?.nextAfterOrdinal ? (
                  <Link
                    aria-label="下一页 ExecutionUnit"
                    href={pageHref(
                      pathname,
                      new URLSearchParams(searchParams.toString()),
                      {
                        after: String(units.data.nextAfterOrdinal),
                        unitId: null,
                        attemptId: null
                      }
                    )}
                  >
                    <ArrowRight size={12} />
                  </Link>
                ) : null}
              </nav>
            </header>

            <div
              className={styles.matrixGroups}
              style={{
                gridTemplateColumns: `repeat(${Math.max(
                  1,
                  groups.length
                )}, minmax(138px, 1fr))`
              }}
            >
              {groups.map((group) => (
                <section key={group.key}>
                  <header>
                    <span>{group.label}</span>
                    <b>{group.units.length}</b>
                  </header>
                  <div>
                    {group.units.map((unit) => (
                      <Link
                        data-tone={unitTone(unit.lifecycle, unit.quality)}
                        aria-current={
                          unit.id === selectedUnit?.id ? "true" : undefined
                        }
                        aria-label={`Execution ${String(unit.ordinal).padStart(
                          3,
                          "0"
                        )} · ${unit.lifecycle} · ${unit.quality}`}
                        title={`Unit ${unit.ordinal} · ${unit.lifecycle} · ${unit.quality}`}
                        href={pageHref(
                          pathname,
                          new URLSearchParams(searchParams.toString()),
                          { unitId: unit.id, attemptId: null }
                        )}
                        key={unit.id}
                      >
                        <i />
                        <span>{unit.ordinal}</span>
                      </Link>
                    ))}
                  </div>
                </section>
              ))}
            </div>

            <div className={styles.legend}>
              {[
                ["running", "Running"],
                ["passed", "Passed"],
                ["failed", "Failed / Blocked"],
                ["infra", "Environment"],
                ["queued", "Queued"],
                ["pending", "Pending"]
              ].map(([tone, label]) => (
                <span key={tone}>
                  <i data-tone={tone} /> {label}
                </span>
              ))}
            </div>

            <div className={styles.eventStream}>
              <span>LATEST VERIFIED FACT</span>
              <p>
                <i />
                U{String(selectedUnit?.ordinal ?? 0).padStart(3, "0")} ·{" "}
                {selectedUnit?.lifecycle} / {selectedUnit?.quality}
              </p>
              <p>
                <i data-risk />
                Attempt {selectedAttempt?.attemptNumber ?? "—"} ·{" "}
                {liveSession?.state ?? "等待 LiveSession"}
              </p>
              <small>
                {snapshot.data
                  ? DATE_FORMAT.format(snapshot.data.observedAt)
                  : "数据库投影持续刷新"}
              </small>
            </div>
          </div>

          <aside className={styles.inspector}>
            <header>
              <span>EXECUTION FOCUS</span>
              <Eye size={14} />
            </header>
            <div className={styles.unitIdentity}>
              <strong>
                EXE-{String(selectedUnit?.ordinal ?? 0).padStart(3, "0")}
              </strong>
              <span>
                {selectedUnit
                  ? caseLabel(selectedCaseVersion, selectedUnit.caseVersionId)
                  : "等待 Unit"}
                {selectedCaseVersion
                  ? ` · ${selectedCaseVersion.roleKey ?? "role"}`
                  : ""}
              </span>
              <em
                data-tone={
                  selectedUnit
                    ? unitTone(
                        selectedUnit.lifecycle,
                        selectedUnit.quality
                      )
                    : "pending"
                }
              >
                {selectedUnit?.lifecycle ?? "PENDING"}
              </em>
            </div>

            <section className={styles.runtimeStage}>
              <header>
                <div>
                  <i />
                  <i />
                  <i />
                </div>
                <span>
                  <ShieldCheck size={11} />{" "}
                  {liveSession?.browserSessionId ?? "等待 LiveSession"}
                </span>
                <MonitorDot size={14} />
              </header>
              <main>
                {snapshot.isPending || attempts.isPending ? (
                  <LoadingState label="正在读取 LiveSession" />
                ) : !selectedAttempt ? (
                  <div className={styles.executionState}>
                    <Clock3 size={22} />
                    <span>WAITING FOR DISPATCH</span>
                    <strong>尚无物理 UnitAttempt</strong>
                    <small>调度后会建立第一次精确尝试</small>
                  </div>
                ) : !liveSession ? (
                  <div className={styles.executionState}>
                    <Globe2 size={22} />
                    <span>SESSION NOT READY</span>
                    <strong>尚未建立 LiveSession</strong>
                    <small>不会使用静态 CRM 画面替代</small>
                  </div>
                ) : (
                  <div className={styles.runtimeFacts}>
                    <div>
                      <span>CONTROL STATE</span>
                      <strong>{liveSession.state}</strong>
                      <small>
                        Browser revision {liveSession.browserRevision}
                      </small>
                    </div>
                    <dl>
                      <div>
                        <dt>Epoch</dt>
                        <dd>{liveSession.controlEpoch}</dd>
                      </div>
                      <div>
                        <dt>Fence</dt>
                        <dd>{liveSession.fencingToken}</dd>
                      </div>
                      <div>
                        <dt>Human</dt>
                        <dd>{liveSession.humanInfluenced ? "YES" : "NO"}</dd>
                      </div>
                    </dl>
                    <p>
                      UnitAttempt 暂无公开 screencast API；这里仅展示服务端可验证的控制事实。
                    </p>
                  </div>
                )}
              </main>
            </section>

            <div className={styles.executionPath}>
              {[
                ["Unit", true],
                ["Attempt", Boolean(selectedAttempt)],
                ["Session", Boolean(liveSession)],
                ["Control", Boolean(liveSession)]
              ].map(([label, done], index) => (
                <span
                  data-state={done ? "done" : index === 1 ? "current" : "wait"}
                  key={String(label)}
                >
                  <i>{done ? <Check size={9} /> : index + 1}</i>
                  {label}
                </span>
              ))}
            </div>

            <section className={styles.attempts}>
              <span>UNIT ATTEMPTS</span>
              {attempts.data?.map((attempt) => (
                <Link
                  aria-current={
                    attempt.id === selectedAttempt?.id ? "page" : undefined
                  }
                  href={pageHref(
                    pathname,
                    new URLSearchParams(searchParams.toString()),
                    { attemptId: attempt.id }
                  )}
                  key={attempt.id}
                >
                  <i>
                    {attempt.lifecycle === "CLOSED" ? (
                      <Check size={10} />
                    ) : (
                      attempt.attemptNumber
                    )}
                  </i>
                  <div>
                    <strong>Attempt {attempt.attemptNumber}</strong>
                    <small>
                      {attempt.lifecycle} · {attempt.quality}
                    </small>
                  </div>
                </Link>
              ))}
            </section>

            <div className={styles.evidence}>
              <button type="button" disabled title="等待 UnitAttempt Evidence listing API">
                <Camera size={14} />
                <span>截图</span>
                <b>GATED</b>
              </button>
              <button type="button" disabled title="等待 UnitAttempt Network listing API">
                <Network size={14} />
                <span>网络</span>
                <b>GATED</b>
              </button>
              <button type="button" disabled title="等待 UnitAttempt Log stream API">
                <Terminal size={14} />
                <span>日志</span>
                <b>GATED</b>
              </button>
            </div>

            <div className={styles.controlNote}>
              <Fingerprint size={16} />
              <div>
                <span>CONTROL FACT</span>
                <strong>{controlIntent(liveSession?.state)}</strong>
              </div>
            </div>

            {snapshot.data?.lease ? (
              <div className={styles.lease}>
                <Fingerprint size={15} />
                <div>
                  <span>{snapshot.data.lease.ownerType} CONTROL LEASE</span>
                  <strong>{snapshot.data.lease.ownerId}</strong>
                  <small>
                    {snapshot.data.lease.state} · 到期{" "}
                    {DATE_FORMAT.format(snapshot.data.lease.expiresAt)}
                  </small>
                </div>
              </div>
            ) : null}
            {snapshot.data?.pendingCommand ? (
              <div className={styles.pendingCommand}>
                <Clock3 size={15} />
                <div>
                  <span>PENDING COMMAND</span>
                  <strong>
                    {snapshot.data.pendingCommand.type} ·{" "}
                    {snapshot.data.pendingCommand.status}
                  </strong>
                </div>
              </div>
            ) : null}

            <div className={styles.liveControls}>
              {activeAttemptControl ? (
                <button
                  type="button"
                  onClick={() =>
                    void submitLiveControl(activeAttemptControl.kind)
                  }
                  disabled={!canOperate || isChangingControl}
                >
                  {activeAttemptControl.icon === "eye" ? (
                    <Eye size={14} />
                  ) : activeAttemptControl.icon === "bot" ? (
                    <Bot size={14} />
                  ) : (
                    <Play size={14} />
                  )}
                  {activeAttemptControl.label}
                </button>
              ) : null}
              {["AGENT_CONTROLLED", "HUMAN_CONTROLLED"].includes(
                liveSession?.state ?? ""
              ) ? (
                <button
                  type="button"
                  onClick={() => void submitLiveControl("pause")}
                  disabled={!canOperate || isChangingControl}
                >
                  <Pause size={14} /> 暂停 Attempt
                </button>
              ) : null}
            </div>
            {errorMessage ? (
              <p className={styles.inlineError} role="alert">
                <CircleAlert size={13} /> {errorMessage}
              </p>
            ) : null}
          </aside>

          <footer className={styles.batchControls}>
            <Link href={`/projects/${projectId}/tasks?runId=${selectedRun.id}`}>
              <Layers3 size={15} /> 切换任务
            </Link>
            {selectedRun.lifecycle === "RUNNING" ? (
              <button
                className={styles.controlMain}
                type="button"
                onClick={() => void submitRunControl("pause")}
                disabled={!canOperate || runControl.isPending}
              >
                <Pause size={15} /> 暂停派发
              </button>
            ) : selectedRun.lifecycle === "PAUSED" ? (
              <button
                className={styles.controlMain}
                type="button"
                onClick={() => void submitRunControl("resume")}
                disabled={!canOperate || runControl.isPending}
              >
                <Play size={15} /> 继续派发
              </button>
            ) : null}
            {activeAttemptControl ? (
              <button
                type="button"
                onClick={() =>
                  void submitLiveControl(activeAttemptControl.kind)
                }
                disabled={!canOperate || isChangingControl}
              >
                <Eye size={15} /> {activeAttemptControl.label}
              </button>
            ) : null}
            <button
              data-danger={cancelArmed}
              type="button"
              onClick={requestCancel}
              disabled={
                !canOperate ||
                runControl.isPending ||
                ["CLOSED", "CANCELING"].includes(selectedRun.lifecycle)
              }
            >
              <CircleStop size={15} />{" "}
              {cancelArmed ? "再次确认取消" : "安全取消"}
            </button>
            <i />
            <span>
              {summary.closed} / {summary.total}
            </span>
            <Link href={`/projects/${projectId}/results?runId=${selectedRun.id}`}>
              查看阶段结果 <ArrowUpRight size={15} />
            </Link>
          </footer>
        </section>
      )}
    </div>
  );
}
