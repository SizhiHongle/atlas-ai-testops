"use client";

import {
  ArrowRight,
  ArrowUpRight,
  Check,
  CircleAlert,
  Layers3,
  Pin,
  ShieldCheck
} from "lucide-react";
import Link from "next/link";
import {
  usePathname,
  useRouter,
  useSearchParams
} from "next/navigation";

import { useSessionQuery } from "@/features/auth/api/auth-queries";
import { ApiProblemError } from "@/shared/api/problem";
import { createRequestId } from "@/shared/api/request-id";
import { ErrorState } from "@/shared/ui/feedback/error-state";
import { LoadingState } from "@/shared/ui/feedback/loading-state";

import {
  useInsightBriefQuery,
  useInsightSnapshotQuery,
  usePinInsightSnapshotMutation
} from "../api/insight-queries";
import type {
  InsightBriefViewModel,
  InsightMetricViewModel
} from "../model/insight";
import styles from "./insight-page.module.css";

const NUMBER_FORMAT = new Intl.NumberFormat("zh-CN");
const METRIC_FORMAT = new Intl.NumberFormat("zh-CN", {
  maximumFractionDigits: 2
});
const DATE_FORMAT = new Intl.DateTimeFormat("zh-CN", {
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit"
});
const TERRAIN_POSITIONS = [
  styles.terrainNodeOne,
  styles.terrainNodeTwo,
  styles.terrainNodeThree,
  styles.terrainNodeFour
];

function metricValue(metric: InsightMetricViewModel): string {
  return metric.percentage === null
    ? "—"
    : `${METRIC_FORMAT.format(metric.percentage)}%`;
}

function deltaValue(value: number | null): string {
  if (value === null) return "不可比较";
  const percentage = value / 100;
  return `较上周期 ${percentage > 0 ? "+" : ""}${METRIC_FORMAT.format(
    percentage
  )}%`;
}

function sampleValue(metric: InsightMetricViewModel): string {
  if (metric.sampleStatus === "NO_DATA") return "无可比样本";
  if (metric.sampleStatus === "LOW_SAMPLE") {
    return `${NUMBER_FORMAT.format(metric.denominator)} 样本 · LOW_SAMPLE`;
  }
  return `${NUMBER_FORMAT.format(metric.denominator)} 条有效样本`;
}

function shortRef(value: string): string {
  const normalized = value.startsWith("sha256:") ? value.slice(7) : value;
  return normalized.slice(0, 8).toUpperCase();
}

function windowHref(
  pathname: string,
  searchParams: URLSearchParams,
  windowDays: 7 | 30 | 90
): string {
  const next = new URLSearchParams(searchParams.toString());
  next.set("window", String(windowDays));
  next.delete("snapshot");
  return `${pathname}?${next.toString()}`;
}

function resultHref(projectId: string, taskRunId: string): string {
  return `/projects/${projectId}/results?runId=${taskRunId}`;
}

function MetricCard({
  label,
  metric,
  delta
}: Readonly<{
  label: string;
  metric: InsightMetricViewModel;
  delta: number | null;
}>) {
  return (
    <div className={styles.metricCard}>
      <span>{label}</span>
      <strong>{metricValue(metric)}</strong>
      <small>{deltaValue(delta)}</small>
    </div>
  );
}

function RiskCard({
  insight,
  projectId
}: Readonly<{
  insight: InsightBriefViewModel;
  projectId: string;
}>) {
  const autonomous = insight.current.autonomousTrustedPassRate;

  if (!insight.activeRisk) {
    return (
      <div className={styles.noRiskCluster}>
        <ShieldCheck size={20} />
        <span>ACTIVE RISK SIGNAL</span>
        <h3>当前 DatasetCut 无非通过 Gate</h3>
        <p>这不是风险为零的推断，只表示当前事实集中没有 activeRisk。</p>
        <footer>
          <span>AUTONOMOUS TRUSTED</span>
          <strong>{metricValue(autonomous)}</strong>
          <small>{sampleValue(autonomous)}</small>
        </footer>
      </div>
    );
  }

  return (
    <div className={styles.riskCluster}>
      <CircleAlert size={20} />
      <span>ACTIVE RISK SIGNAL</span>
      <h3>{insight.activeRisk.taskPlanName}</h3>
      <p>
        {insight.activeRisk.gateDecision} ·{" "}
        {insight.activeRisk.reasonCount} 条 Gate reason
      </p>
      <Link href={resultHref(projectId, insight.activeRisk.taskRunId)}>
        进入任务结果 <ArrowRight size={14} />
      </Link>
      <footer>
        <span>AUTONOMOUS TRUSTED</span>
        <strong>{metricValue(autonomous)}</strong>
        <small>{deltaValue(insight.deltas.autonomousTrustedPassRate)}</small>
      </footer>
    </div>
  );
}

export function InsightPage({ projectId }: Readonly<{ projectId: string }>) {
  const pathname = usePathname();
  const router = useRouter();
  const searchParams = useSearchParams();
  const session = useSessionQuery();
  const snapshotId = searchParams.get("snapshot");
  const parsedWindow = Number(searchParams.get("window") ?? "30");
  const requestedWindow: 7 | 30 | 90 =
    parsedWindow === 7 || parsedWindow === 90 ? parsedWindow : 30;
  const brief = useInsightBriefQuery(
    projectId,
    requestedWindow,
    !snapshotId
  );
  const snapshot = useInsightSnapshotQuery(snapshotId);
  const query = snapshotId ? snapshot : brief;
  const pin = usePinInsightSnapshotMutation(projectId);

  if (query.isPending) {
    return (
      <LoadingState
        label={
          snapshotId
            ? "正在读取固定的 InsightSnapshot"
            : "正在计算质量洞察"
        }
      />
    );
  }
  if (query.isError) {
    return (
      <ErrorState
        detail={query.error.message}
        onRetry={() => void query.refetch()}
      />
    );
  }
  if (!query.data) {
    return <LoadingState label="正在读取质量洞察" />;
  }

  const insight = query.data;
  const windowDays = insight.windowDays;
  const pinError =
    pin.error instanceof ApiProblemError
      ? pin.error.problem.detail
      : pin.error?.message;

  async function pinSnapshot() {
    if (insight.mode === "PINNED") return;
    try {
      const pinned = await pin.mutateAsync({
        windowDays,
        asOf: insight.datasetCut.asOf.toISOString(),
        clientMutationId: `pin-insight-${createRequestId()}`
      });
      router.push(`${pathname}?snapshot=${pinned.id}`);
    } catch {
      // Mutation state keeps the backend Problem Details in this page.
    }
  }

  const windowParams = new URLSearchParams(searchParams.toString());
  const terrain = insight.terrain.slice(0, 4);

  return (
    <div className={styles.page}>
      <header className={styles.hero}>
        <div>
          <p>
            <Layers3 size={13} /> QUALITY TERRAIN · {windowDays} DAYS
          </p>
          <h1>把失败放回它发生的旅程里。</h1>
          <span>
            洞察跨越多个 Task 观察质量趋势；发布判断仍然回到每一次冻结任务的真实结果。
          </span>
        </div>
        <div className={styles.heroActions}>
          {insight.activeRisk ? (
            <Link
              href={resultHref(projectId, insight.activeRisk.taskRunId)}
            >
              查看最新风险任务 <ArrowUpRight size={15} />
            </Link>
          ) : (
            <button
              type="button"
              disabled
              title="当前 DatasetCut 未返回 activeRisk；这不表示风险为零"
            >
              当前无风险任务 <ShieldCheck size={15} />
            </button>
          )}
        </div>
      </header>

      <section
        className={styles.terrainStage}
        data-mode={insight.mode}
      >
        <header className={styles.terrainTitle}>
          <span>
            {insight.mode === "PINNED"
              ? `SNAPSHOT ${shortRef(insight.snapshot!.id)}`
              : "LIVE BRIEF"}{" "}
            · QUALITY SIGNALS
          </span>
          <strong>
            {NUMBER_FORMAT.format(insight.current.executionUnitCount)}
          </strong>
          <small>次 Execution · {windowDays} 天质量地形</small>
        </header>

        <nav className={styles.windowNav} aria-label="洞察窗口">
          {([7, 30, 90] as const).map((value) => (
            <Link
              aria-current={windowDays === value ? "page" : undefined}
              href={windowHref(pathname, windowParams, value)}
              key={value}
              title={
                insight.mode === "PINNED"
                  ? `离开固定 Snapshot，读取 ${value} 天实时窗口`
                  : `读取 ${value} 天窗口`
              }
            >
              {value}D
            </Link>
          ))}
        </nav>

        <div className={styles.qualitySphere}>
          <div className={styles.sphereSurface} />
          <div className={`${styles.sphereRing} ${styles.ringA}`} />
          <div className={`${styles.sphereRing} ${styles.ringB}`} />
          <div className={`${styles.sphereGrid} ${styles.gridA}`} />
          <div className={`${styles.sphereGrid} ${styles.gridB}`} />
          <svg viewBox="0 0 620 620" aria-hidden="true">
            <path d="M105 340 C178 184 303 160 370 246 S482 408 535 252" />
            <path d="M145 430 C258 330 338 370 476 180" />
          </svg>

          {terrain.map((item, index) => {
            const risk =
              insight.activeRisk?.taskPlanId === item.taskPlanId;
            return (
              <Link
                className={`${styles.terrainNode} ${
                  TERRAIN_POSITIONS[index] ?? ""
                } ${risk ? styles.riskNode : ""}`}
                href={resultHref(projectId, item.latestTaskRunId)}
                key={item.taskPlanId}
                title={`${item.taskRunCount} TaskRuns · ${item.executionUnitCount} ExecutionUnits · ${item.trustedPassRate.sampleStatus}`}
              >
                <i />
                <span>{item.label}</span>
                <b>{metricValue(item.trustedPassRate)}</b>
              </Link>
            );
          })}

          {!terrain.length ? (
            <div className={styles.emptyTerrain}>
              <span>NO COMPARABLE SLICES</span>
              <strong>当前窗口没有质量地形</strong>
              <small>
                只有 Fully Resolved 或 Reevaluated ResultSnapshot
                才进入洞察数据集。
              </small>
            </div>
          ) : null}
        </div>

        <aside className={styles.terrainMetrics}>
          <MetricCard
            label="稳定可信通过"
            metric={insight.current.trustedPassRate}
            delta={insight.deltas.trustedPassRate}
          />
          <MetricCard
            label="方法健康度"
            metric={insight.current.methodHealthRate}
            delta={insight.deltas.methodHealthRate}
          />
          <RiskCard insight={insight} projectId={projectId} />
        </aside>

        <div className={styles.traceCard}>
          <header
            title={`SourceSet ${insight.datasetCut.sourceSetDigest}\nQuery ${insight.datasetCut.queryHash}\nAuth ${insight.datasetCut.authScopeHash}`}
          >
            <span>
              DATASET TRACE ·{" "}
              {insight.mode === "PINNED" ? "FROZEN" : "LIVE CUT"}
            </span>
            <h3>
              {insight.mode === "PINNED"
                ? `InsightSnapshot · ${shortRef(insight.snapshot!.id)}`
                : `DatasetCut · ${shortRef(
                    insight.datasetCut.sourceSetDigest
                  )}`}
            </h3>
            <small>
              Query {shortRef(insight.datasetCut.queryHash)} · Policy{" "}
              {insight.metricPolicyVersion}
            </small>
          </header>

          <div className={styles.tracePath}>
            {[
              {
                label: "Source snapshots",
                value: String(insight.datasetCut.sourceSnapshotCount)
              },
              {
                label: "Gate decisions",
                value: String(insight.datasetCut.gateDecisionCount)
              },
              { label: "Aggregation", value: "ratio Σ" },
              {
                label: "Current runs",
                value: String(insight.current.taskRunCount)
              },
              {
                label: "Baseline runs",
                value: String(insight.baseline.taskRunCount)
              },
              {
                label: "Dataset as of",
                value: DATE_FORMAT.format(insight.datasetCut.asOf)
              }
            ].map((step) => (
              <div className={styles.traceStep} key={step.label}>
                <i />
                <span>{step.label}</span>
                <small>{step.value}</small>
              </div>
            ))}
          </div>

          <button
            className={styles.pinButton}
            type="button"
            aria-label={
              insight.mode === "PINNED"
                ? "当前 InsightSnapshot 已固定"
                : "固定当前 DatasetCut"
            }
            disabled={
              insight.mode === "PINNED" || !session.data || pin.isPending
            }
            onClick={() => void pinSnapshot()}
            title={
              insight.mode === "PINNED"
                ? `Snapshot ${insight.snapshot!.snapshotHash}`
                : "把当前 DatasetCut 固定为不可变 InsightSnapshot"
            }
          >
            {insight.mode === "PINNED" ? (
              <Check size={16} />
            ) : (
              <Pin size={16} />
            )}
          </button>

          {pinError ? (
            <p className={styles.pinError} role="alert">
              {pinError}
            </p>
          ) : null}
        </div>
      </section>
    </div>
  );
}
