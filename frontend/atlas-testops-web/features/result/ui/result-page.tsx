"use client";

import {
  ArrowRight,
  Check,
  CircleAlert,
  FileText,
  Filter,
  GitCompareArrows,
  RefreshCw,
  ShieldCheck,
  Sparkles
} from "lucide-react";
import Link from "next/link";
import {
  usePathname,
  useRouter,
  useSearchParams
} from "next/navigation";
import {
  useEffect,
  useState
} from "react";

import { useSessionQuery } from "@/features/auth/api/auth-queries";
import { useTaskRunsQuery } from "@/features/task/api/task-queries";
import { ApiProblemError } from "@/shared/api/problem";
import { createRequestId } from "@/shared/api/request-id";
import { ErrorState } from "@/shared/ui/feedback/error-state";
import { LoadingState } from "@/shared/ui/feedback/loading-state";

import {
  useEvaluateTaskGateMutation,
  useFailureClustersQuery,
  useTaskResultQuery
} from "../api/result-queries";
import type {
  FailureClusterViewModel,
  TaskResultViewModel
} from "../model/result";
import { ReviewClassificationDialog } from "./review-classification-dialog";
import styles from "./result-page.module.css";

const RESULT_REVIEWERS = new Set([
  "ORG_ADMIN",
  "PROJECT_ADMIN",
  "CASE_REVIEWER"
]);

const DATE_FORMAT = new Intl.DateTimeFormat("zh-CN", {
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit"
});

function shortId(value: string): string {
  return value.slice(0, 8).toUpperCase();
}

function shortRef(value: string): string {
  return value.slice(-6).toUpperCase();
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

function percentage(value: number | null): string {
  return value === null ? "—" : `${value}%`;
}

function clusterTone(domain: string): "coral" | "violet" | "sand" | "neutral" {
  if (domain === "PRODUCT") return "coral";
  if (["TEST_SPEC", "TEST_DATA", "AGENT_AUTOMATION"].includes(domain)) {
    return "violet";
  }
  if (
    [
      "IDENTITY",
      "ENVIRONMENT",
      "INFRASTRUCTURE",
      "EXTERNAL_DEPENDENCY"
    ].includes(domain)
  ) {
    return "sand";
  }
  return "neutral";
}

function gateRecommendation(
  gate: TaskResultViewModel["gate"]
): string {
  if (!gate) return "等待显式质量门禁评估";
  if (gate.decision === "ACCEPTED") return "建议进入下一道发布门";
  if (gate.decision === "REJECTED") return "阻止本次发布";
  return "等待证据与归因闭环";
}

function evidenceLabel(
  evidence: FailureClusterViewModel["classification"] extends infer T
    ? T extends { supportingEvidenceRefs: Array<infer R> }
      ? R
      : never
    : never
): string {
  return `${evidence.kind} · ${shortRef(evidence.refId)}`;
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
      data-tone={clusterTone(cluster.signal.domain)}
      href={href}
      aria-current={selected ? "true" : undefined}
    >
      <span>{cluster.signal.domain}</span>
      <strong>{cluster.signal.code}</strong>
      <small>
        {cluster.classification?.hypothesis ??
          `${cluster.signal.outcomeClass} · ${cluster.signal.stability}`}
      </small>
      <b>{cluster.affectedCount}</b>
      <i aria-hidden="true">
        <em
          style={{ width: `${cluster.classification?.confidence ?? 0}%` }}
        />
      </i>
      <footer>
        {cluster.classification
          ? `${cluster.classification.authorKind} · ${cluster.classification.confidence}%`
          : "等待归因投影"}
      </footer>
    </Link>
  );
}

function ResultEmptyState({
  projectId,
  title,
  detail
}: Readonly<{
  projectId: string;
  title: string;
  detail: string;
}>) {
  return (
    <section className={styles.resultEmpty}>
      <div aria-hidden="true">
        <i />
        <i />
        <ShieldCheck size={28} />
        <span>RESULT</span>
      </div>
      <section>
        <span>IMMUTABLE RESULT CENTER</span>
        <h2>{title}</h2>
        <p>{detail}</p>
        <Link href={`/projects/${projectId}/tasks`}>
          查看任务中心 <ArrowRight size={13} />
        </Link>
      </section>
    </section>
  );
}

export function ResultPage({ projectId }: Readonly<{ projectId: string }>) {
  const pathname = usePathname();
  const router = useRouter();
  const searchParams = useSearchParams();
  const searchParamsText = searchParams.toString();
  const session = useSessionQuery();
  const runs = useTaskRunsQuery(projectId);
  const requestedRunId = searchParams.get("runId");
  const requestedRun =
    runs.data?.find((run) => run.id === requestedRunId) ?? null;
  const selectedRun =
    requestedRun ??
    runs.data?.find((run) => run.lifecycle === "CLOSED") ??
    runs.data?.[0] ??
    null;
  const result = useTaskResultQuery(selectedRun?.id ?? null);
  const snapshotId = result.data?.snapshot.id ?? null;
  const cursor = searchParams.get("cursor");
  const clusters = useFailureClustersQuery(snapshotId, cursor);
  const requestedClusterId = searchParams.get("clusterId");
  const requestedCluster =
    clusters.data?.items.find(
      (cluster) => cluster.id === requestedClusterId
    ) ?? null;
  const selectedCluster =
    requestedCluster ?? clusters.data?.items[0] ?? null;
  const gateMutation = useEvaluateTaskGateMutation(selectedRun?.id ?? null);
  const [reviewOpen, setReviewOpen] = useState(false);
  const canReview =
    session.data?.roles.some((role) => RESULT_REVIEWERS.has(role)) ?? false;

  useEffect(() => {
    if (!selectedRun || requestedRunId === selectedRun.id) return;
    router.replace(
      pageHref(pathname, new URLSearchParams(searchParamsText), {
        runId: selectedRun.id,
        clusterId: null,
        cursor: null
      })
    );
  }, [
    pathname,
    requestedRunId,
    router,
    searchParamsText,
    selectedRun
  ]);

  useEffect(() => {
    if (
      !selectedCluster ||
      requestedClusterId === selectedCluster.id ||
      result.isPending
    ) {
      return;
    }
    router.replace(
      pageHref(pathname, new URLSearchParams(searchParamsText), {
        clusterId: selectedCluster.id
      })
    );
  }, [
    pathname,
    requestedClusterId,
    result.isPending,
    router,
    searchParamsText,
    selectedCluster
  ]);

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
            <span>
              区分产品问题、测试方法、环境失败与 Flaky，并把每个判断连接回真实证据。
            </span>
          </div>
        </header>
        <ResultEmptyState
          projectId={projectId}
          title="还没有可查看的结果"
          detail="TaskRun 完成 Oracle 聚合后会形成不可变 ResultSnapshot；结果中心不会根据运行中状态推测正式结论。"
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
    try {
      await gateMutation.mutateAsync({
        resultSnapshotId: snapshotId,
        gatePolicyVersion: "0.1.0",
        clientMutationId: `evaluate-gate-${createRequestId()}`
      });
    } catch {
      // Mutation state renders the backend problem without escalating it.
    }
  }

  const accepted = result.data?.gate?.decision === "ACCEPTED";

  return (
    <div className={styles.page}>
      <header className={styles.hero}>
        <div>
          <p>
            <ShieldCheck size={13} /> TASK RESULT · RUN-
            {shortId(selectedRun.id)}
          </p>
          <h1>
            {accepted
              ? "这次回归，可以进入下一道发布门。"
              : "结果不是一张报表，而是一次发布决定。"}
          </h1>
          <span>
            区分产品问题、测试方法、环境失败与 Flaky，并把每个判断连接回真实证据。
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
          <button
            type="button"
            disabled
            title="当前后端只支持基础设施失败的受控重跑，不能在结果页伪装成通用失败重跑"
          >
            <RefreshCw size={15} />{" "}
            {accepted ? "相同配置再跑" : "重跑失败单元"}
          </button>
        </div>
      </header>

      {!result.data ? (
        <ResultEmptyState
          projectId={projectId}
          title="这条 TaskRun 尚无 ResultSnapshot"
          detail="结果聚合只在 UnitResolution 事实满足门禁后产生；当前 TaskRun 仍可从下方切换，页面不会用临时执行状态冒充正式结果。"
        />
      ) : (
        <>
          <section className={styles.gateHero}>
            <div
              className={styles.gateCore}
              data-decision={result.data.gate?.decision ?? "NOT_EVALUATED"}
            >
              <span>QUALITY GATE</span>
              <div>
                <i />
                <i />
                <strong>{result.data.gate?.decision ?? "PENDING"}</strong>
                <small>
                  可信通过{" "}
                  {percentage(
                    result.data.snapshot.trustedPassRate.percentage
                  )}
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
                <small>Manifest 守恒</small>
              </div>
              <div>
                <span>可信通过</span>
                <strong>
                  {result.data.snapshot.trustedPassRate.numerator}
                </strong>
                <small>
                  {percentage(
                    result.data.snapshot.trustedPassRate.percentage
                  )}{" "}
                  / {result.data.snapshot.trustedPassRate.denominator}
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
                  {result.data.snapshot.verdicts.inconclusive +
                    result.data.snapshot.verdicts.notEvaluated}
                </strong>
                <small>
                  {result.data.snapshot.verdicts.inconclusive} /{" "}
                  {result.data.snapshot.verdicts.notEvaluated}
                </small>
              </div>
            </div>

            <aside className={styles.verdict}>
              <header>
                <ShieldCheck size={20} />
                <em>GATE VERDICT</em>
              </header>
              <span>发布建议</span>
              <h2>{gateRecommendation(result.data.gate)}</h2>
              <p>
                {result.data.gate?.reasons.length
                  ? result.data.gate.reasons
                      .map((reason) => `${reason.code} × ${reason.count}`)
                      .join("；")
                  : "由后端固定 Gate Policy 对当前 exact ResultSnapshot 显式评估，不由前端推测。"}
              </p>
              <small>
                Snapshot r{result.data.snapshot.revision}
                {result.data.gate
                  ? ` · Policy ${result.data.gate.policyVersion}`
                  : ` · ${result.data.snapshot.finality}`}
              </small>
              <button
                type="button"
                onClick={() => void evaluateGate()}
                disabled={!canReview || gateMutation.isPending}
                title={
                  canReview
                    ? "对当前 exact ResultSnapshot 追加 Gate Decision"
                    : "需要 CASE_REVIEWER 或 Project Admin 权限"
                }
              >
                <RefreshCw size={14} />{" "}
                {gateMutation.isPending
                  ? "正在评估…"
                  : result.data.gate
                    ? "重新评估门禁"
                    : "评估质量门禁"}
              </button>
              {gateError ? <b role="alert">{gateError}</b> : null}
            </aside>
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
                <ShieldCheck size={30} />
                <strong>
                  {percentage(
                    result.data.snapshot.trustedPassRate.percentage
                  )}
                </strong>
                <span>TRUSTED</span>
              </div>
              <section>
                <span>NO FAILURE CLUSTERS</span>
                <h2>当前 ResultSnapshot 没有失败聚类。</h2>
                <p>
                  聚类投影没有发现当前失败信号；发布结论仍以绑定 exact
                  Snapshot 与 Classification 集合的 TaskGateDecision 为准。
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
                  <button
                    type="button"
                    disabled
                    title="服务端当前未开放按类型筛选参数"
                  >
                    <Filter size={14} /> 全部类型
                  </button>
                </header>
                <div className={styles.clusterList}>
                  {clusters.data.items.map((cluster) => (
                    <ClusterCard
                      cluster={cluster}
                      selected={cluster.id === selectedCluster?.id}
                      href={pageHref(
                        pathname,
                        new URLSearchParams(searchParamsText),
                        { clusterId: cluster.id }
                      )}
                      key={cluster.id}
                    />
                  ))}
                </div>
                <footer>
                  {cursor ? (
                    <Link
                      href={pageHref(
                        pathname,
                        new URLSearchParams(searchParamsText),
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
                        new URLSearchParams(searchParamsText),
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
                    <span>OUTCOME CONSTELLATION</span>
                    <strong>{selectedCluster?.signal.code}</strong>
                  </div>
                  <em>{selectedCluster?.signal.domain}</em>
                </header>
                <div className={styles.clusterMap}>
                  <i />
                  <i />
                  <div>
                    <CircleAlert size={22} />
                    <strong>{selectedCluster?.affectedCount}</strong>
                    <span>AFFECTED UNITS</span>
                  </div>
                  {selectedCluster?.affectedUnitResolutionRevisionIds
                    .slice(0, 5)
                    .map((resolutionId, index) => (
                      <span
                        data-position={index + 1}
                        data-representative={
                          resolutionId ===
                          selectedCluster.representativeUnitResolutionRevisionId
                            ? "true"
                            : undefined
                        }
                        key={resolutionId}
                        title={resolutionId}
                      >
                        <i /> UNIT {shortRef(resolutionId)}
                      </span>
                    ))}
                </div>
                <section>
                  <span>ROOT CAUSE SIGNAL · IMMUTABLE REVISION</span>
                  <code>{selectedCluster?.fingerprint}</code>
                  <p>
                    {selectedCluster?.classification?.hypothesis ??
                      "当前 Cluster 尚无可复核 Classification。"}
                  </p>
                  <small>
                    {selectedCluster?.signal.closureReason} ·{" "}
                    {selectedCluster?.signal.stability}
                  </small>
                </section>
              </div>

              <aside className={styles.triage}>
                <header>
                  <span>TRIAGE &amp; EVIDENCE</span>
                  <GitCompareArrows size={15} />
                </header>
                <div className={styles.confidence}>
                  <span>归因置信度</span>
                  <strong>
                    {selectedCluster?.classification
                      ? `${selectedCluster.classification.confidence}%`
                      : "—"}
                  </strong>
                  <i aria-hidden="true">
                    <b
                      style={{
                        width: `${selectedCluster?.classification?.confidence ?? 0}%`
                      }}
                    />
                  </i>
                  <small>
                    {selectedCluster?.classification
                      ? `${selectedCluster.classification.judgmentState} · r${selectedCluster.classification.revision}`
                      : "等待 Classification"}
                  </small>
                </div>
                <div className={styles.evidence}>
                  <span>证据与影响</span>
                  {selectedCluster?.classification?.supportingEvidenceRefs
                    .slice(0, 3)
                    .map((evidence) => (
                      <small key={`${evidence.kind}-${evidence.refId}`}>
                        <Check size={11} /> {evidenceLabel(evidence)}
                      </small>
                    ))}
                  {selectedCluster?.classification?.contradictingEvidenceRefs
                    .slice(0, 1)
                    .map((evidence) => (
                      <small
                        data-contradicting
                        key={`${evidence.kind}-${evidence.refId}`}
                      >
                        <CircleAlert size={11} /> 反证{" "}
                        {evidenceLabel(evidence)}
                      </small>
                    ))}
                  {!selectedCluster?.classification
                    ?.supportingEvidenceRefs.length ? (
                    <>
                      <small>
                        <Check size={11} /> Evidence{" "}
                        {selectedCluster?.signal.evidenceCompleteness}
                      </small>
                      <small>
                        <Check size={11} /> Integrity{" "}
                        {selectedCluster?.signal.evidenceIntegrity}
                      </small>
                    </>
                  ) : null}
                  <small>
                    <Check size={11} /> Hygiene{" "}
                    {selectedCluster?.signal.dataHygiene}
                  </small>
                  <small>
                    <Check size={11} /> Cluster r
                    {selectedCluster?.revision} · Affected{" "}
                    {selectedCluster?.affectedCount}
                  </small>
                </div>
                <button
                  className={styles.triagePrimary}
                  type="button"
                  onClick={() => setReviewOpen(true)}
                  disabled={!canReview || !selectedCluster?.classification}
                  title={
                    !canReview
                      ? "需要 CASE_REVIEWER 或 Project Admin 权限"
                      : selectedCluster?.classification
                        ? "追加人工 Classification Revision"
                        : "后端没有为未分类 Cluster 暴露 Classification root 创建接口"
                  }
                >
                  <Sparkles size={15} /> 复核失败归因
                </button>
                <button
                  type="button"
                  disabled
                  title="当前后端没有 Known Issue 写入接口"
                >
                  <CircleAlert size={15} /> 标记已知问题
                </button>
                <button
                  type="button"
                  disabled
                  title="当前后端没有测试方法候选写入接口"
                >
                  <GitCompareArrows size={15} /> 提交方法候选
                </button>
              </aside>
            </section>
          )}

          <footer className={styles.snapshotRail}>
            <label>
              <span>RESULT SNAPSHOT</span>
              <select
                aria-label="切换结果任务"
                value={selectedRun.id}
                onChange={(event) =>
                  router.push(
                    pageHref(
                      pathname,
                      new URLSearchParams(searchParamsText),
                      {
                        runId: event.target.value,
                        clusterId: null,
                        cursor: null
                      }
                    )
                  )
                }
              >
                {runs.data.map((run) => (
                  <option value={run.id} key={run.id}>
                    RUN-{shortId(run.id)} · {run.lifecycle} · {run.quality}
                  </option>
                ))}
              </select>
            </label>
            <div>
              <small>
                Finality · Policy v
                {result.data.snapshot.aggregationPolicyVersion}
              </small>
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
              <small>Evidence verified</small>
              <strong>
                {result.data.snapshot.axes.evidenceIntegrity.verified ?? 0} /{" "}
                {result.data.snapshot.manifestCount}
              </strong>
            </div>
            <div>
              <small>Created</small>
              <strong>
                {DATE_FORMAT.format(result.data.snapshot.createdAt)}
              </strong>
            </div>
            <button
              type="button"
              disabled
              title="当前后端未开放通用失败单元重跑"
            >
              <RefreshCw size={14} /> 重跑失败单元
            </button>
          </footer>

          <ReviewClassificationDialog
            key={selectedCluster?.classification?.id ?? "no-classification"}
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
