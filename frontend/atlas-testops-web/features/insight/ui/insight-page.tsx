"use client";

import {
  ArrowRight,
  ArrowUpRight,
  CircleAlert,
  CircleCheck,
  GitCompareArrows,
  Layers3,
  Pin,
  Radio,
  ShieldCheck,
  Sparkles
} from "lucide-react";
import Link from "next/link";
import {
  usePathname,
  useSearchParams
} from "next/navigation";

import { useSessionQuery } from "@/features/auth/api/auth-queries";
import { ApiProblemError } from "@/shared/api/problem";
import { createRequestId } from "@/shared/api/request-id";
import { EmptyState } from "@/shared/ui/feedback/empty-state";
import { ErrorState } from "@/shared/ui/feedback/error-state";
import { LoadingState } from "@/shared/ui/feedback/loading-state";

import {
  useInsightBriefQuery,
  usePinInsightSnapshotMutation
} from "../api/insight-queries";
import type { InsightMetricViewModel } from "../model/insight";
import styles from "./insight-page.module.css";

const DATE_FORMAT = new Intl.DateTimeFormat("zh-CN", {
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit"
});

function metricValue(metric: InsightMetricViewModel): string {
  return metric.percentage === null ? "—" : `${metric.percentage}%`;
}

function deltaValue(value: number | null): string {
  if (value === null) return "不可比较";
  const percentage = value / 100;
  return `${percentage > 0 ? "+" : ""}${percentage}%`;
}

function windowHref(
  pathname: string,
  searchParams: URLSearchParams,
  windowDays: 7 | 30 | 90
): string {
  const next = new URLSearchParams(searchParams.toString());
  next.set("window", String(windowDays));
  return `${pathname}?${next.toString()}`;
}

export function InsightPage({ projectId }: Readonly<{ projectId: string }>) {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const session = useSessionQuery();
  const parsedWindow = Number(searchParams.get("window") ?? "30");
  const windowDays: 7 | 30 | 90 =
    parsedWindow === 7 || parsedWindow === 90 ? parsedWindow : 30;
  const brief = useInsightBriefQuery(projectId, windowDays);
  const pin = usePinInsightSnapshotMutation(projectId);

  if (brief.isPending) return <LoadingState label="正在计算质量洞察" />;
  if (brief.isError) {
    return (
      <ErrorState
        detail={brief.error.message}
        onRetry={() => void brief.refetch()}
      />
    );
  }

  const pinError =
    pin.error instanceof ApiProblemError
      ? pin.error.problem.detail
      : pin.error?.message;

  async function pinSnapshot() {
    if (!brief.data) return;
    await pin.mutateAsync({
      windowDays,
      asOf: brief.data.datasetCut.asOf.toISOString(),
      clientMutationId: `pin-insight-${createRequestId()}`
    });
  }

  return (
    <div className={styles.page}>
      <header className={styles.hero}>
        <div>
          <p>
            <Layers3 size={13} /> QUALITY TERRAIN · {windowDays} DAYS
          </p>
          <h1>把失败放回它发生的旅程里。</h1>
          <span>
            洞察跨越多个 Task 观察质量变化；发布判断仍回到每个不可变
            ResultSnapshot。
          </span>
        </div>
        <div className={styles.heroActions}>
          <nav aria-label="洞察窗口">
            {([7, 30, 90] as const).map((value) => (
              <Link
                aria-current={windowDays === value ? "page" : undefined}
                href={windowHref(
                  pathname,
                  new URLSearchParams(searchParams.toString()),
                  value
                )}
                key={value}
              >
                {value}D
              </Link>
            ))}
          </nav>
          <button
            type="button"
            onClick={() => void pinSnapshot()}
            disabled={!session.data || pin.isPending}
          >
            <Pin size={15} /> 固定 DatasetCut
          </button>
        </div>
      </header>

      <section className={styles.datasetBanner}>
        <div>
          <Radio size={15} />
          <span>DATASET CUT</span>
          <strong>{DATE_FORMAT.format(brief.data.datasetCut.asOf)}</strong>
        </div>
        <p>
          {brief.data.datasetCut.sourceSnapshotCount} ResultSnapshots ·{" "}
          {brief.data.datasetCut.gateDecisionCount} GateDecisions
        </p>
        <em>{brief.data.current.trustedPassRate.sampleStatus}</em>
      </section>

      <section className={styles.terrainStage}>
        <div className={styles.terrainMap}>
          <header>
            <div>
              <span>PROJECT QUALITY TERRAIN</span>
              <strong>TaskPlan 质量地形</strong>
            </div>
            <small>{brief.data.terrain.length} slices · ratio of sums</small>
          </header>
          {brief.data.terrain.length ? (
            <div className={styles.landscape}>
              <i className={styles.gridOne} />
              <i className={styles.gridTwo} />
              {brief.data.terrain.map((item, index) => {
                const rate = item.trustedPassRate.percentage;
                return (
                  <Link
                    className={styles.terrainNode}
                    data-position={index + 1}
                    href={`/projects/${projectId}/results?runId=${item.latestTaskRunId}`}
                    key={item.taskPlanId}
                  >
                    <span>{item.label}</span>
                    <strong>{rate === null ? "—" : `${rate}%`}</strong>
                    <small>
                      {item.taskRunCount} Runs · {item.executionUnitCount} Units
                    </small>
                    <i
                      style={{
                        height: rate === null ? "8%" : `${Math.max(8, rate)}%`
                      }}
                    />
                  </Link>
                );
              })}
              <div className={styles.terrainCaption}>
                <Sparkles size={14} />
                <span>仅呈现 API 返回的最多四个 TaskPlan quality slices</span>
              </div>
            </div>
          ) : (
            <EmptyState
              title="当前窗口没有质量地形"
              detail="只有 Fully Resolved 或 Reevaluated ResultSnapshot 才进入洞察数据集。"
            />
          )}
        </div>

        <aside className={styles.metrics}>
          <article>
            <span>稳定可信通过</span>
            <strong>{metricValue(brief.data.current.trustedPassRate)}</strong>
            <small>{deltaValue(brief.data.deltas.trustedPassRate)}</small>
            <i>
              <b
                style={{
                  width: `${brief.data.current.trustedPassRate.percentage ?? 0}%`
                }}
              />
            </i>
          </article>
          <article>
            <span>自主可信通过</span>
            <strong>
              {metricValue(brief.data.current.autonomousTrustedPassRate)}
            </strong>
            <small>
              {deltaValue(brief.data.deltas.autonomousTrustedPassRate)}
            </small>
            <i>
              <b
                style={{
                  width: `${
                    brief.data.current.autonomousTrustedPassRate.percentage ?? 0
                  }%`
                }}
              />
            </i>
          </article>
          <article>
            <span>方法健康度</span>
            <strong>{metricValue(brief.data.current.methodHealthRate)}</strong>
            <small>{deltaValue(brief.data.deltas.methodHealthRate)}</small>
            <i>
              <b
                style={{
                  width: `${brief.data.current.methodHealthRate.percentage ?? 0}%`
                }}
              />
            </i>
          </article>

          {brief.data.activeRisk ? (
            <article className={styles.riskCard}>
              <CircleAlert size={20} />
              <span>ACTIVE RISK SIGNAL</span>
              <h3>{brief.data.activeRisk.taskPlanName}</h3>
              <p>
                {brief.data.activeRisk.gateDecision} ·{" "}
                {brief.data.activeRisk.reasonCount} gate reasons
              </p>
              <Link
                href={`/projects/${projectId}/results?runId=${brief.data.activeRisk.taskRunId}`}
              >
                进入任务结果 <ArrowRight size={14} />
              </Link>
            </article>
          ) : (
            <article className={styles.noRisk}>
              <ShieldCheck size={23} />
              <span>ACTIVE RISK SIGNAL</span>
              <h3>当前 DatasetCut 无非通过 Gate</h3>
              <p>这不是风险为零的推断，只表示 API 未返回 activeRisk。</p>
            </article>
          )}
        </aside>
      </section>

      <section className={styles.compare}>
        <header>
          <div>
            <GitCompareArrows size={16} />
            <span>CURRENT VS BASELINE</span>
          </div>
          <strong>
            {DATE_FORMAT.format(brief.data.current.startAt)} —{" "}
            {DATE_FORMAT.format(brief.data.current.endAt)}
          </strong>
        </header>
        {[
          {
            label: "Trusted Pass",
            current: brief.data.current.trustedPassRate,
            baseline: brief.data.baseline.trustedPassRate,
            delta: brief.data.deltas.trustedPassRate
          },
          {
            label: "Autonomous Trusted",
            current: brief.data.current.autonomousTrustedPassRate,
            baseline: brief.data.baseline.autonomousTrustedPassRate,
            delta: brief.data.deltas.autonomousTrustedPassRate
          },
          {
            label: "Method Health",
            current: brief.data.current.methodHealthRate,
            baseline: brief.data.baseline.methodHealthRate,
            delta: brief.data.deltas.methodHealthRate
          }
        ].map((metric) => (
          <article key={metric.label}>
            <span>{metric.label}</span>
            <div>
              <i
                style={{ width: `${metric.baseline.percentage ?? 0}%` }}
                title={`Baseline ${metricValue(metric.baseline)}`}
              />
              <b
                style={{ width: `${metric.current.percentage ?? 0}%` }}
                title={`Current ${metricValue(metric.current)}`}
              />
            </div>
            <strong>{metricValue(metric.current)}</strong>
            <small>{deltaValue(metric.delta)}</small>
          </article>
        ))}
        <aside>
          <span>样本范围</span>
          <strong>{brief.data.current.taskRunCount} TaskRuns</strong>
          <small>{brief.data.current.executionUnitCount} ExecutionUnits</small>
        </aside>
      </section>

      <footer className={styles.snapshotRail}>
        <div>
          <span>DATASET PROVENANCE</span>
          <strong>{brief.data.datasetCut.sourceSetDigest}</strong>
        </div>
        <div>
          <CircleCheck size={12} />
          <span>Query Hash</span>
          <strong>{brief.data.datasetCut.queryHash}</strong>
        </div>
        <div>
          <CircleCheck size={12} />
          <span>Projection</span>
          <strong>
            {brief.data.datasetCut.projectionWatermark
              ? DATE_FORMAT.format(brief.data.datasetCut.projectionWatermark)
              : "NO SOURCE WATERMARK"}
          </strong>
        </div>
        <div>
          <CircleCheck size={12} />
          <span>Generated</span>
          <strong>{DATE_FORMAT.format(brief.data.generatedAt)}</strong>
        </div>
        {pin.data ? (
          <Link href={`/projects/${projectId}/insights?snapshot=${pin.data.id}`}>
            Snapshot {pin.data.id.slice(0, 8)} <ArrowUpRight size={13} />
          </Link>
        ) : (
          <em>{pinError ?? "Live brief · not pinned"}</em>
        )}
      </footer>
    </div>
  );
}
