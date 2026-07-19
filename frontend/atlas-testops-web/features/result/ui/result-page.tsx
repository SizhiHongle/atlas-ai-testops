"use client";

import {
  ArrowRight,
  BrainCircuit,
  Check,
  CircleAlert,
  FileText,
  Filter,
  GitCompareArrows,
  Radio,
  RefreshCw,
  ShieldCheck,
  Sparkles
} from "lucide-react";
import Link from "next/link";
import {
  usePathname,
  useSearchParams
} from "next/navigation";
import { useState, type CSSProperties } from "react";

import { useSessionQuery } from "@/features/auth/api/auth-queries";
import { useTaskRunsQuery } from "@/features/task/api/task-queries";
import { ApiProblemError } from "@/shared/api/problem";
import { createRequestId } from "@/shared/api/request-id";
import { EmptyState } from "@/shared/ui/feedback/empty-state";
import { ErrorState } from "@/shared/ui/feedback/error-state";
import { LoadingState } from "@/shared/ui/feedback/loading-state";

import {
  useEvaluateTaskGateMutation,
  useFailureClustersQuery,
  useTaskResultQuery
} from "../api/result-queries";
import type { FailureClusterViewModel } from "../model/result";
import { ReviewClassificationDialog } from "./review-classification-dialog";
import styles from "./result-page.module.css";

const RESULT_REVIEWERS = new Set([
  "ORG_ADMIN",
  "PROJECT_ADMIN",
  "CASE_REVIEWER"
]);

type GateProgressStyle = CSSProperties & { "--gate-progress": string };

const DATE_FORMAT = new Intl.DateTimeFormat("zh-CN", {
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit"
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

function percentage(value: number | null): string {
  return value === null ? "—" : `${value}%`;
}

function AxisCard({
  label,
  values
}: Readonly<{ label: string; values: Record<string, number> }>) {
  const entries = Object.entries(values);
  const total = entries.reduce((sum, [, value]) => sum + value, 0);
  return (
    <article className={styles.axisCard}>
      <span>{label}</span>
      <strong>{total}</strong>
      <div>
        {entries.map(([key, value]) => (
          <i key={key} title={`${key}: ${value}`}>
            <b style={{ width: total ? `${(value / total) * 100}%` : "0%" }} />
            <small>
              {key} <em>{value}</em>
            </small>
          </i>
        ))}
      </div>
    </article>
  );
}

function ClusterCard({
  cluster,
  selected,
  href
}: Readonly<{
  cluster: FailureClusterViewModel;
  selected: boolean;
  href: string;
}>) {
  return (
    <Link
      className={`${styles.clusterCard} ${selected ? styles.selectedCluster : ""}`}
      href={href}
    >
      <span>{cluster.signal.domain}</span>
      <strong>{cluster.signal.code}</strong>
      <small>
        {cluster.classification?.hypothesis ??
          `${cluster.signal.outcomeClass} · ${cluster.signal.stability}`}
      </small>
      <b>{cluster.affectedCount}</b>
      <i>
        <em
          style={{ width: `${cluster.classification?.confidence ?? 0}%` }}
        />
      </i>
      <footer>
        {cluster.classification
          ? `${cluster.classification.authorKind} ${cluster.classification.confidence}%`
          : "UNCLASSIFIED"}
      </footer>
    </Link>
  );
}

export function ResultPage({ projectId }: Readonly<{ projectId: string }>) {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const session = useSessionQuery();
  const runs = useTaskRunsQuery(projectId);
  const selectedRunId =
    searchParams.get("runId") ??
    runs.data?.find((run) => run.lifecycle === "CLOSED")?.id ??
    runs.data?.[0]?.id ??
    null;
  const selectedRun =
    runs.data?.find((run) => run.id === selectedRunId) ?? runs.data?.[0] ?? null;
  const result = useTaskResultQuery(selectedRun?.id ?? null);
  const snapshotId = result.data?.snapshot.id ?? null;
  const cursor = searchParams.get("cursor");
  const clusters = useFailureClustersQuery(snapshotId, cursor);
  const selectedClusterId =
    searchParams.get("clusterId") ?? clusters.data?.items[0]?.id ?? null;
  const selectedCluster =
    clusters.data?.items.find((cluster) => cluster.id === selectedClusterId) ??
    clusters.data?.items[0] ??
    null;
  const gateMutation = useEvaluateTaskGateMutation(selectedRun?.id ?? null);
  const [reviewOpen, setReviewOpen] = useState(false);
  const canReview =
    session.data?.roles.some((role) => RESULT_REVIEWERS.has(role)) ?? false;

  if (runs.isPending) return <LoadingState label="正在读取结果任务" />;
  if (runs.isError) {
    return (
      <ErrorState
        detail={runs.error.message}
        onRetry={() => void runs.refetch()}
      />
    );
  }

  if (!selectedRun) {
    return (
      <div className={styles.page}>
        <header className={styles.hero}>
          <div>
            <p>
              <ShieldCheck size={13} /> TASK RESULT
            </p>
            <h1>结果等待第一条正式 TaskRun。</h1>
          </div>
        </header>
        <EmptyState
          title="还没有可查看的结果"
          detail="TaskRun 完成 Oracle 聚合后会形成不可变 ResultSnapshot。"
        />
      </div>
    );
  }

  if (result.isPending) return <LoadingState label="正在读取 ResultSnapshot" />;
  if (result.isError) {
    return (
      <ErrorState
        detail={result.error.message}
        onRetry={() => void result.refetch()}
      />
    );
  }

  const gateError =
    gateMutation.error instanceof ApiProblemError
      ? gateMutation.error.problem.detail
      : gateMutation.error?.message;

  async function evaluateGate() {
    if (!snapshotId) return;
    await gateMutation.mutateAsync({
      resultSnapshotId: snapshotId,
      gatePolicyVersion: "0.1.0",
      clientMutationId: `evaluate-gate-${createRequestId()}`
    });
  }

  return (
    <div className={styles.page}>
      <header className={styles.hero}>
        <div>
          <p>
            <ShieldCheck size={13} /> TASK RESULT · RUN-{shortId(selectedRun.id)}
          </p>
          <h1>结果不是一张报表，而是一次发布决定。</h1>
          <span>
            区分产品、测试方法、环境与 Flaky，并把每个判断连接回不可变证据。
          </span>
        </div>
        <div className={styles.heroActions}>
          <button
            type="button"
            disabled
            title="后端尚未开放完整证据包导出 API"
          >
            <FileText size={15} /> 导出证据
          </button>
          <Link href={`/projects/${projectId}/live?runId=${selectedRun.id}`}>
            <Radio size={15} /> 回到现场
          </Link>
        </div>
      </header>

      <nav className={styles.runStrip} aria-label="TaskRun Results">
        <span>RESULT RUNS</span>
        {runs.data.slice(0, 6).map((run) => (
          <Link
            href={pageHref(
              pathname,
              new URLSearchParams(searchParams.toString()),
              { runId: run.id, clusterId: null, cursor: null }
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

      {!result.data ? (
        <EmptyState
          title="这条 TaskRun 尚无 ResultSnapshot"
          detail="结果聚合只在 UnitResolution 事实满足门禁后产生；页面不会根据运行中 Unit 推测正式结果。"
        />
      ) : (
        <>
          <section className={styles.gateHero}>
            <div
              className={styles.gateCore}
              data-decision={result.data.gate?.decision ?? "NOT_EVALUATED"}
              style={
                {
                  "--gate-progress": `${
                    result.data.snapshot.trustedPassRate.percentage ?? 0
                  }%`
                } as GateProgressStyle
              }
            >
              <span>QUALITY GATE</span>
              <div>
                <i />
                <i />
                <strong>{result.data.gate?.decision ?? "PENDING"}</strong>
                <small>
                  Trusted {percentage(result.data.snapshot.trustedPassRate.percentage)}
                </small>
              </div>
              <p>
                Snapshot r{result.data.snapshot.revision} ·{" "}
                {result.data.snapshot.finality}
              </p>
            </div>

            <div className={styles.scoreGrid}>
              <div>
                <span>执行单元</span>
                <strong>{result.data.snapshot.manifestCount}</strong>
                <small>Manifest conserving</small>
              </div>
              <div>
                <span>通过</span>
                <strong>{result.data.snapshot.verdicts.passed}</strong>
                <small>
                  Raw {percentage(result.data.snapshot.rawPassRate.percentage)}
                </small>
              </div>
              <div data-risk>
                <span>失败</span>
                <strong>{result.data.snapshot.verdicts.failed}</strong>
                <small>Verdict = FAILED</small>
              </div>
              <div>
                <span>不确定 / 未评估</span>
                <strong>
                  {result.data.snapshot.verdicts.inconclusive} /{" "}
                  {result.data.snapshot.verdicts.notEvaluated}
                </strong>
                <small>Explicit result states</small>
              </div>
            </div>

            <aside className={styles.verdict}>
              <header>
                <BrainCircuit size={20} />
                <em>GATE FACT</em>
              </header>
              <span>发布判断</span>
              <h3>{result.data.gate?.decision ?? "尚未评估"}</h3>
              <p>
                {result.data.gate?.reasons.length
                  ? result.data.gate.reasons
                      .map((reason) => `${reason.code} × ${reason.count}`)
                      .join("；")
                  : "使用后端固定 Gate Policy 对当前 exact ResultSnapshot 进行评估。"}
              </p>
              <button
                type="button"
                onClick={() => void evaluateGate()}
                disabled={!canReview || gateMutation.isPending}
              >
                <RefreshCw size={14} />{" "}
                {result.data.gate ? "重新评估门禁" : "评估质量门禁"}
              </button>
              {gateError ? <small role="alert">{gateError}</small> : null}
            </aside>
          </section>

          <section className={styles.axisTerrain}>
            <header>
              <div>
                <span>RESULT AXES</span>
                <strong>结果六轴地形</strong>
              </div>
              <small>
                Projection {DATE_FORMAT.format(result.data.projectionWatermark)}
              </small>
            </header>
            <div>
              <AxisCard
                label="OUTCOME CLASS"
                values={result.data.snapshot.axes.outcomeClass}
              />
              <AxisCard
                label="EXECUTION INFLUENCE"
                values={result.data.snapshot.axes.executionInfluence}
              />
              <AxisCard
                label="STABILITY"
                values={result.data.snapshot.axes.stability}
              />
              <AxisCard
                label="EVIDENCE COMPLETENESS"
                values={result.data.snapshot.axes.evidenceCompleteness}
              />
              <AxisCard
                label="EVIDENCE INTEGRITY"
                values={result.data.snapshot.axes.evidenceIntegrity}
              />
              <AxisCard
                label="DATA HYGIENE"
                values={result.data.snapshot.axes.dataHygiene}
              />
            </div>
          </section>

          {clusters.isPending ? (
            <LoadingState label="正在读取失败聚类" />
          ) : clusters.isError ? (
            <ErrorState
              detail={clusters.error.message}
              onRetry={() => void clusters.refetch()}
            />
          ) : !clusters.data?.items.length ? (
            <section className={styles.clearState}>
              <div>
                <i />
                <i />
                <ShieldCheck size={34} />
                <strong>
                  {percentage(result.data.snapshot.trustedPassRate.percentage)}
                </strong>
                <span>TRUSTED</span>
              </div>
              <section>
                <span>NO FAILURE CLUSTERS</span>
                <h2>当前 ResultSnapshot 没有失败聚类。</h2>
                <p>
                  这表示聚类投影未发现当前失败信号；发布结论仍以 TaskGateDecision
                  为准。
                </p>
              </section>
            </section>
          ) : (
            <section className={styles.workspace}>
              <div className={styles.clusterDeck}>
                <header>
                  <div>
                    <span>FAILURE CLUSTERS</span>
                    <strong>失败聚类</strong>
                  </div>
                  <button type="button" disabled>
                    <Filter size={14} /> 当前页
                  </button>
                </header>
                {clusters.data.items.map((cluster) => (
                  <ClusterCard
                    cluster={cluster}
                    selected={cluster.id === selectedCluster?.id}
                    href={pageHref(
                      pathname,
                      new URLSearchParams(searchParams.toString()),
                      { clusterId: cluster.id }
                    )}
                    key={cluster.id}
                  />
                ))}
                <footer>
                  {cursor ? (
                    <Link
                      href={pageHref(
                        pathname,
                        new URLSearchParams(searchParams.toString()),
                        { cursor: null, clusterId: null }
                      )}
                    >
                      返回第一页
                    </Link>
                  ) : null}
                  {clusters.data.nextCursor ? (
                    <Link
                      href={pageHref(
                        pathname,
                        new URLSearchParams(searchParams.toString()),
                        {
                          cursor: clusters.data.nextCursor,
                          clusterId: null
                        }
                      )}
                    >
                      下一页 <ArrowRight size={12} />
                    </Link>
                  ) : null}
                </footer>
              </div>

              <div className={styles.constellation}>
                <header>
                  <div>
                    <span>IMPACT MAP</span>
                    <strong>{selectedCluster?.signal.code}</strong>
                  </div>
                  <em>{selectedCluster?.signal.domain}</em>
                </header>
                <div className={styles.clusterMap}>
                  <i />
                  <i />
                  <div>
                    <CircleAlert size={23} />
                    <strong>{selectedCluster?.affectedCount}</strong>
                    <span>AFFECTED</span>
                  </div>
                  {[
                    selectedCluster?.signal.outcomeClass,
                    selectedCluster?.signal.stability,
                    selectedCluster?.signal.verdict,
                    selectedCluster?.signal.closureReason
                  ].map((label, index) => (
                    <span data-position={index + 1} key={`${label}-${index}`}>
                      <i /> {label}
                    </span>
                  ))}
                </div>
                <section>
                  <span>ROOT CAUSE SIGNAL</span>
                  <code>{selectedCluster?.fingerprint}</code>
                  <p>
                    {selectedCluster?.classification?.hypothesis ??
                      "当前 Cluster 尚无可复核 Classification。"}
                  </p>
                </section>
              </div>

              <aside className={styles.triage}>
                <header>
                  <span>TRIAGE FACTS</span>
                  <GitCompareArrows size={15} />
                </header>
                <div className={styles.confidence}>
                  <span>归因置信度</span>
                  <strong>
                    {selectedCluster?.classification?.confidence ?? "—"}%
                  </strong>
                  <i>
                    <b
                      style={{
                        width: `${selectedCluster?.classification?.confidence ?? 0}%`
                      }}
                    />
                  </i>
                </div>
                <div className={styles.evidence}>
                  <span>不可变事实</span>
                  <small>
                    <Check size={11} /> Cluster revision{" "}
                    {selectedCluster?.revision}
                  </small>
                  <small>
                    <Check size={11} /> Affected{" "}
                    {selectedCluster?.affectedCount}
                  </small>
                  <small>
                    <Check size={11} /> Supporting evidence{" "}
                    {selectedCluster?.classification?.supportingEvidenceRefs
                      .length ?? 0}
                  </small>
                  <small>
                    <Check size={11} /> Judgment{" "}
                    {selectedCluster?.classification?.judgmentState ??
                      "UNCLASSIFIED"}
                  </small>
                </div>
                <button
                  className={styles.triagePrimary}
                  type="button"
                  onClick={() => setReviewOpen(true)}
                  disabled={!canReview || !selectedCluster?.classification}
                  title={
                    selectedCluster?.classification
                      ? "追加人工 Classification Revision"
                      : "后端没有为未分类 Cluster 暴露 Classification root 创建接口"
                  }
                >
                  <Sparkles size={15} /> 复核归因
                </button>
                <button
                  type="button"
                  disabled
                  title="当前后端没有缺陷系统写入接口"
                >
                  <CircleAlert size={15} /> 创建产品缺陷
                </button>
              </aside>
            </section>
          )}

          <footer className={styles.snapshotRail}>
            <div>
              <span>IMMUTABLE SNAPSHOT</span>
              <strong>r{result.data.snapshot.revision}</strong>
            </div>
            <div>
              <small>Finality</small>
              <strong>{result.data.snapshot.finality}</strong>
            </div>
            <div>
              <small>Autonomous</small>
              <strong>
                {percentage(
                  result.data.snapshot.autonomousPassRate.percentage
                )}
              </strong>
            </div>
            <div>
              <small>Decisive</small>
              <strong>
                {percentage(result.data.snapshot.decisivePassRate.percentage)}
              </strong>
            </div>
            <div>
              <small>Created</small>
              <strong>{DATE_FORMAT.format(result.data.snapshot.createdAt)}</strong>
            </div>
            <button
              type="button"
              disabled
              title="后端仅开放基础设施失败重跑，需在任务控制中显式触发"
            >
              <RefreshCw size={14} /> 重跑失败单元
            </button>
          </footer>

          <ReviewClassificationDialog
            snapshotId={snapshotId}
            cluster={selectedCluster}
            open={reviewOpen}
            onClose={() => setReviewOpen(false)}
          />
        </>
      )}
    </div>
  );
}
