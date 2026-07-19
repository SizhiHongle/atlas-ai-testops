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

import { useSessionQuery } from "@/features/auth/api/auth-queries";
import {
  useExecutionUnitsQuery,
  useTaskRunCommandMutation,
  useTaskRunsQuery
} from "@/features/task/api/task-queries";
import { summarizeExecutionUnits } from "@/features/task/model/task-mapper";
import { ApiProblemError } from "@/shared/api/problem";
import { createRequestId } from "@/shared/api/request-id";
import { EmptyState } from "@/shared/ui/feedback/empty-state";
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
  return `${pathname}?${next.toString()}`;
}

function unitTone(lifecycle: string, quality: string): string {
  if (lifecycle === "RUNNING") return "active";
  if (lifecycle === "QUEUED") return "queued";
  if (quality === "PASSED") return "good";
  if (["FAILED", "BLOCKED", "INFRA_ERROR"].includes(quality)) return "risk";
  return "muted";
}

export function LivePage({ projectId }: Readonly<{ projectId: string }>) {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const session = useSessionQuery();
  const runs = useTaskRunsQuery(projectId);
  const selectedRunId =
    searchParams.get("runId") ??
    runs.data?.find((run) => run.lifecycle !== "CLOSED")?.id ??
    runs.data?.[0]?.id ??
    null;
  const selectedRun =
    runs.data?.find((run) => run.id === selectedRunId) ?? runs.data?.[0] ?? null;
  const parsedAfter = Number(searchParams.get("after") ?? "0");
  const afterOrdinal =
    Number.isSafeInteger(parsedAfter) && parsedAfter >= 0 ? parsedAfter : 0;
  const units = useExecutionUnitsQuery(selectedRun?.id ?? null, afterOrdinal);
  const unitItems = units.data?.items ?? [];
  const selectedUnitId =
    searchParams.get("unitId") ??
    unitItems.find((unit) => unit.lifecycle === "RUNNING")?.id ??
    unitItems[0]?.id ??
    null;
  const selectedUnit =
    unitItems.find((unit) => unit.id === selectedUnitId) ?? unitItems[0] ?? null;
  const attempts = useUnitAttemptsQuery(
    selectedRun?.id ?? null,
    selectedUnit?.id ?? null
  );
  const selectedAttemptId =
    searchParams.get("attemptId") ?? attempts.data?.[0]?.id ?? null;
  const selectedAttempt =
    attempts.data?.find((attempt) => attempt.id === selectedAttemptId) ??
    attempts.data?.[0] ??
    null;
  const snapshot = useLiveSnapshotQuery(selectedAttempt?.id ?? null);
  const liveControl = useLiveControlMutation(selectedAttempt?.id ?? null);
  const runControl = useTaskRunCommandMutation(projectId);
  const summary = summarizeExecutionUnits(unitItems);
  const canOperate =
    session.data?.roles.some((role) => RUN_OPERATORS.has(role)) ?? false;

  if (runs.isPending) return <LoadingState label="正在读取批量现场" />;
  if (runs.isError) {
    return (
      <ErrorState
        detail={runs.error.message}
        onRetry={() => void runs.refetch()}
      />
    );
  }

  const dataError =
    units.error ?? attempts.error ?? snapshot.error ?? liveControl.error;
  const errorMessage =
    dataError instanceof ApiProblemError
      ? dataError.problem.detail
      : dataError?.message;
  const liveSession = snapshot.data?.session ?? null;
  const isChangingControl =
    liveControl.isPending || Boolean(snapshot.data?.pendingCommand);

  async function submitLiveControl(kind: LiveControlKind) {
    if (!liveSession) return;
    await liveControl.mutateAsync({
      controlEpoch: liveSession.controlEpoch,
      kind,
      command: {
        reason: `Requested from Atlas live console: ${kind}.`,
        requestedTtlSec: kind === "takeover" ? 300 : null
      }
    });
  }

  async function submitRunControl(kind: "pause" | "resume" | "cancel") {
    if (!selectedRun) return;
    await runControl.mutateAsync({
      runId: selectedRun.id,
      revision: selectedRun.revision,
      kind,
      command: {
        clientMutationId: `${kind}-run-${createRequestId()}`
      }
    });
  }

  if (!selectedRun) {
    return (
      <div className={styles.page}>
        <header className={styles.hero}>
          <div>
            <p>
              <MonitorDot size={13} /> TASK CONTROL
            </p>
            <h1>批量现场等待第一条真实运行。</h1>
            <span>TaskRun 启动后，ExecutionUnit 与 LiveSession 会在这里出现。</span>
          </div>
        </header>
        <EmptyState
          title="还没有 TaskRun"
          detail="先从任务中心选择一个已发布 TaskPlanVersion 并创建批量任务。"
        />
      </div>
    );
  }

  return (
    <div className={styles.page}>
      <header className={styles.hero}>
        <div>
          <p>
            <MonitorDot size={13} /> TASK CONTROL · RUN-{shortId(selectedRun.id)}
          </p>
          <h1>
            {selectedRun.unitCount ?? "—"} 条执行，正在同一片现场发生。
          </h1>
          <span>
            从 TaskRun 全局观察真实 ExecutionUnit，再进入 exact UnitAttempt
            检查控制权与浏览器会话。
          </span>
        </div>
        <em data-state={selectedRun.lifecycle}>
          <Radio size={11} /> {selectedRun.lifecycle}
        </em>
      </header>

      <nav className={styles.runStrip} aria-label="TaskRun">
        <span>RECENT RUNS</span>
        {runs.data.slice(0, 6).map((run) => (
          <Link
            href={pageHref(
              pathname,
              new URLSearchParams(searchParams.toString()),
              {
                runId: run.id,
                unitId: null,
                attemptId: null,
                after: null
              }
            )}
            aria-current={run.id === selectedRun.id ? "page" : undefined}
            key={run.id}
          >
            <strong>RUN-{shortId(run.id)}</strong>
            <small>
              {run.lifecycle} · {run.quality}
            </small>
          </Link>
        ))}
      </nav>

      <section className={styles.contextBanner}>
        <div>
          <Rocket size={18} />
          <span>TASK RUN</span>
          <strong>TaskPlanVersion {shortId(selectedRun.taskPlanVersionId)}</strong>
        </div>
        <p>当前现场只读取本次运行冻结事实；后续草稿和新版本不会改变它。</p>
        <Link href={`/projects/${projectId}/results?runId=${selectedRun.id}`}>
          查看正式结果 <ArrowRight size={13} />
        </Link>
      </section>

      <section className={styles.liveRibbon}>
        <div className={styles.progressCore}>
          <span>CURRENT PAGE</span>
          <strong>{units.isPending ? "…" : `${summary.progress}%`}</strong>
        </div>
        <div>
          <span>当前页关闭</span>
          <strong>
            {summary.closed} / {summary.total}
          </strong>
          <small>每页最多 100 Units</small>
        </div>
        <div>
          <span>通过</span>
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
        <EmptyState
          title="当前页没有 ExecutionUnit"
          detail={
            selectedRun.materializationState === "MATERIALIZING"
              ? "TaskRun 仍在可恢复物化中，页面会自动刷新。"
              : "请返回上一页或检查 TaskRun Manifest。"
          }
        />
      ) : (
        <section className={styles.console}>
          <aside className={styles.workerRail}>
            <header>
              <span>UNIT LANES</span>
              <b>{String(unitItems.length).padStart(2, "0")}</b>
            </header>
            <div>
              {unitItems.slice(0, 12).map((unit) => (
                <Link
                  data-tone={unitTone(unit.lifecycle, unit.quality)}
                  aria-current={unit.id === selectedUnit?.id ? "page" : undefined}
                  href={pageHref(
                    pathname,
                    new URLSearchParams(searchParams.toString()),
                    { unitId: unit.id, attemptId: null }
                  )}
                  key={unit.id}
                >
                  <span>U{String(unit.ordinal).padStart(3, "0")}</span>
                  <div>
                    <strong>{unit.lifecycle}</strong>
                    <small>{unit.quality}</small>
                  </div>
                  <i />
                </Link>
              ))}
            </div>
            <footer>
              <span>页窗口</span>
              <strong>
                {afterOrdinal + 1}—{afterOrdinal + unitItems.length}
              </strong>
            </footer>
          </aside>

          <div className={styles.matrixPanel}>
            <header>
              <div>
                <span>EXECUTION MATRIX</span>
                <strong>按 Unit Ordinal · exact backend page</strong>
              </div>
              <nav>
                {afterOrdinal > 0 ? (
                  <Link
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
                    <ArrowLeft size={13} /> 上一页
                  </Link>
                ) : null}
                {units.data?.nextAfterOrdinal ? (
                  <Link
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
                    下一页 <ArrowRight size={13} />
                  </Link>
                ) : null}
              </nav>
            </header>

            <div className={styles.matrixGrid}>
              {unitItems.map((unit) => (
                <Link
                  data-tone={unitTone(unit.lifecycle, unit.quality)}
                  aria-current={unit.id === selectedUnit?.id ? "true" : undefined}
                  aria-label={`Execution Unit ${unit.ordinal} · ${unit.lifecycle} · ${unit.quality}`}
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

            <div className={styles.legend}>
              {[
                ["active", "Running"],
                ["good", "Passed"],
                ["risk", "Failed / Infra"],
                ["queued", "Queued"],
                ["muted", "Other"]
              ].map(([tone, label]) => (
                <span key={tone}>
                  <i data-tone={tone} /> {label}
                </span>
              ))}
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
                  <EmptyState
                    title="尚无 UnitAttempt"
                    detail="ExecutionUnit 进入调度后，首个物理 Attempt 会出现在这里。"
                  />
                ) : !liveSession ? (
                  <EmptyState
                    title="尚未建立 LiveSession"
                    detail="当前 Attempt 没有正式浏览器控制投影；不会用静态 CRM 画面代替。"
                  />
                ) : (
                  <div className={styles.runtimeFacts}>
                    <div>
                      <span>CONTROL STATE</span>
                      <strong>{liveSession.state}</strong>
                      <small>
                        Observed {DATE_FORMAT.format(snapshot.data!.observedAt)}
                      </small>
                    </div>
                    <dl>
                      <div>
                        <dt>Control Epoch</dt>
                        <dd>{liveSession.controlEpoch}</dd>
                      </div>
                      <div>
                        <dt>Fencing Token</dt>
                        <dd>{liveSession.fencingToken}</dd>
                      </div>
                      <div>
                        <dt>Browser Revision</dt>
                        <dd>{liveSession.browserRevision}</dd>
                      </div>
                      <div>
                        <dt>Human Influenced</dt>
                        <dd>{liveSession.humanInfluenced ? "YES" : "NO"}</dd>
                      </div>
                    </dl>
                    <p>
                      浏览器像素流与动作事件尚无公共 streaming
                      API；本页只展示可验证的控制面事实。
                    </p>
                  </div>
                )}
              </main>
            </section>
          </div>

          <aside className={styles.inspector}>
            <header>
              <span>EXECUTION FOCUS</span>
              <Eye size={14} />
            </header>
            {selectedUnit ? (
              <>
                <div className={styles.unitIdentity}>
                  <strong>U{String(selectedUnit.ordinal).padStart(3, "0")}</strong>
                  <span>CASE-{shortId(selectedUnit.caseVersionId)}</span>
                  <em data-tone={unitTone(selectedUnit.lifecycle, selectedUnit.quality)}>
                    {selectedUnit.lifecycle}
                  </em>
                </div>
                <div className={styles.axisGrid}>
                  <div>
                    <span>LIFECYCLE</span>
                    <strong>{selectedUnit.lifecycle}</strong>
                  </div>
                  <div>
                    <span>QUALITY</span>
                    <strong>{selectedUnit.quality}</strong>
                  </div>
                  <div>
                    <span>HYGIENE</span>
                    <strong>{selectedUnit.hygiene}</strong>
                  </div>
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
                  <button type="button" disabled title="等待 Evidence listing 公共 API">
                    <Camera size={14} /> 截图
                  </button>
                  <button
                    type="button"
                    disabled
                    title="等待 Network trace listing 公共 API"
                  >
                    <Network size={14} /> 网络
                  </button>
                  <button
                    type="button"
                    disabled
                    title="等待 Runtime log streaming 公共 API"
                  >
                    <Terminal size={14} /> 日志
                  </button>
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
                {errorMessage ? (
                  <p className={styles.inlineError} role="alert">
                    {errorMessage}
                  </p>
                ) : null}
                <div className={styles.liveControls}>
                  {liveSession?.state === "AGENT_CONTROLLED" ? (
                    <button
                      type="button"
                      onClick={() => void submitLiveControl("takeover")}
                      disabled={!canOperate || isChangingControl}
                    >
                      <Eye size={14} /> 接管
                    </button>
                  ) : liveSession?.state === "HUMAN_CONTROLLED" ? (
                    <button
                      type="button"
                      onClick={() => void submitLiveControl("return")}
                      disabled={!canOperate || isChangingControl}
                    >
                      <Bot size={14} /> 交还 Agent
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
                  ) : liveSession?.state === "PAUSED" ? (
                    <button
                      type="button"
                      onClick={() => void submitLiveControl("resume")}
                      disabled={!canOperate || isChangingControl}
                    >
                      <Play size={14} /> 恢复 Attempt
                    </button>
                  ) : null}
                </div>
              </>
            ) : null}
          </aside>

          <footer className={styles.batchControls}>
            <span>
              <CircleAlert size={14} /> TaskRun Revision {selectedRun.revision}
            </span>
            {selectedRun.lifecycle === "RUNNING" ? (
              <button
                type="button"
                onClick={() => void submitRunControl("pause")}
                disabled={!canOperate || runControl.isPending}
              >
                <Pause size={15} /> 暂停派发
              </button>
            ) : selectedRun.lifecycle === "PAUSED" ? (
              <button
                type="button"
                onClick={() => void submitRunControl("resume")}
                disabled={!canOperate || runControl.isPending}
              >
                <Play size={15} /> 继续派发
              </button>
            ) : null}
            <button
              type="button"
              onClick={() => void submitRunControl("cancel")}
              disabled={
                !canOperate ||
                runControl.isPending ||
                ["CLOSED", "CANCELING"].includes(selectedRun.lifecycle)
              }
            >
              <CircleStop size={15} /> 取消 TaskRun
            </button>
            <i />
            <Link href={`/projects/${projectId}/results?runId=${selectedRun.id}`}>
              查看阶段结果 <ArrowUpRight size={15} />
            </Link>
          </footer>
        </section>
      )}
    </div>
  );
}
