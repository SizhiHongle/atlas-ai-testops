"use client";

import {
  ArrowRight,
  ArrowUpRight,
  Atom,
  CircleAlert,
  CircleCheck,
  Fingerprint,
  GitBranch,
  Play,
  Radio,
  Rocket,
  Sparkles
} from "lucide-react";
import Link from "next/link";

import { useSessionQuery } from "@/features/auth/api/auth-queries";
import { useCaseCatalogQuery } from "@/features/case/api/case-queries";
import { useFixtureCatalogQuery } from "@/features/fixture/api/fixture-queries";
import { useIdentityWalletQuery } from "@/features/identity/api/identity-queries";
import { useInsightBriefQuery } from "@/features/insight/api/insight-queries";
import {
  useTaskPlansQuery,
  useTaskRunsQuery
} from "@/features/task/api/task-queries";
import { LoadingState } from "@/shared/ui/feedback/loading-state";

import styles from "./space-dashboard.module.css";

function shortId(value: string): string {
  return value.slice(0, 8).toUpperCase();
}

function metricLabel(value: number | null): string {
  return value === null ? "—" : `${value}%`;
}

export function SpaceDashboard({
  projectId
}: Readonly<{ projectId: string }>) {
  const session = useSessionQuery();
  const identities = useIdentityWalletQuery(projectId);
  const fixtures = useFixtureCatalogQuery(projectId);
  const cases = useCaseCatalogQuery(projectId);
  const plans = useTaskPlansQuery(projectId);
  const runs = useTaskRunsQuery(projectId);
  const insight = useInsightBriefQuery(projectId, 30);

  if (!session.data) return <LoadingState label="正在进入测试空间" />;

  const basePath = `/projects/${projectId}`;
  const activeRuns = runs.data?.filter((run) => run.lifecycle !== "CLOSED") ?? [];
  const validCases = cases.data?.filter((testCase) => testCase.graphValid) ?? [];
  const latestRun = runs.data?.[0] ?? null;
  const risk = insight.data?.activeRisk ?? null;

  return (
    <div className={styles.page}>
      <section className={styles.hero}>
        <div>
          <p className={styles.eyebrow}>
            <Sparkles size={13} aria-hidden="true" />
            {session.data.workspace.projectName} · QUALITY SPACE
          </p>
          <h1>测试，不再是一张报表。</h1>
          <p className={styles.lead}>
            让身份、数据、Agent 与证据在同一个任务空间中自然连接。
          </p>
          <div className={styles.actions}>
            <Link className={styles.primary} href={`${basePath}/insights`}>
              查看质量轨迹 <ArrowRight size={16} />
            </Link>
            <Link className={styles.secondary} href={`${basePath}/cases`}>
              打开用例工作室
            </Link>
          </div>
        </div>
        <article className={styles.orbitCard}>
          <span>ACTIVE SPACE</span>
          <strong>{session.data.workspace.projectName}</strong>
          <p>
            {activeRuns.length} Active Runs · {plans.data?.length ?? "—"} Plans
          </p>
          <i />
        </article>
      </section>

      <section className={styles.bento}>
        <article className={styles.caseCard}>
          <header>
            <div>
              <span>TEST SPACE / CASE CATALOG</span>
              <h2>真实用例工作台</h2>
            </div>
            <em>
              <GitBranch size={12} /> {validCases.length} VALID
            </em>
          </header>
          <div className={styles.caseOrbit}>
            <i />
            <i />
            <div>
              <strong>{cases.data?.length ?? "—"}</strong>
              <span>TEST CASES</span>
            </div>
            {(cases.data ?? []).slice(0, 4).map((testCase, index) => (
              <Link
                data-position={index + 1}
                href={`${basePath}/cases?caseId=${testCase.id}`}
                key={testCase.id}
              >
                {testCase.key}
              </Link>
            ))}
          </div>
          <footer>
            <span>{validCases.length} 图验证通过</span>
            <span>{(cases.data?.length ?? 0) - validCases.length} 待修正</span>
            <Link href={`${basePath}/cases`}>
              进入用例 <ArrowRight size={14} />
            </Link>
          </footer>
        </article>

        <article className={styles.runDeck}>
          <header>
            <div>
              <span>AGENT QUEUE</span>
              <h2>运行牌组</h2>
            </div>
            <Rocket size={18} />
          </header>
          <div>
            {(runs.data ?? []).slice(0, 3).map((run, index) => (
              <Link
                data-position={index + 1}
                data-active={run.lifecycle !== "CLOSED"}
                href={`${basePath}/${run.lifecycle === "CLOSED" ? "results" : "live"}?runId=${run.id}`}
                key={run.id}
              >
                <span>RUN-{shortId(run.id)}</span>
                <strong>{run.lifecycle}</strong>
                <small>
                  {run.triggerSource} · {run.quality}
                </small>
                {run.lifecycle !== "CLOSED" ? <Radio size={11} /> : null}
              </Link>
            ))}
            {!runs.data?.length ? (
              <p>TaskRun Catalog 暂无记录。</p>
            ) : null}
          </div>
          <Link href={`${basePath}/tasks`}>
            打开任务中心 <ArrowRight size={14} />
          </Link>
        </article>

        <Link className={styles.identityCard} href={`${basePath}/identities`}>
          <header>
            <div>
              <span>IDENTITY WALLET</span>
              <h2>身份容量</h2>
            </div>
            <Fingerprint size={18} />
          </header>
          <strong>{identities.data?.totals.available ?? "—"}</strong>
          <span>可用账号</span>
          <footer>
            <i>租用 {identities.data?.totals.leased ?? "—"}</i>
            <i>隔离 {identities.data?.totals.quarantined ?? "—"}</i>
          </footer>
        </Link>

        <Link className={styles.fixtureCard} href={`${basePath}/fixtures/atoms`}>
          <header>
            <div>
              <span>FIXTURE LIBRARY</span>
              <h2>能力资产</h2>
            </div>
            <Atom size={18} />
          </header>
          <div>
            <strong>{fixtures.data?.atoms.length ?? "—"}</strong>
            <span>Atoms</span>
          </div>
          <div>
            <strong>{fixtures.data?.blueprints.length ?? "—"}</strong>
            <span>Blueprints</span>
          </div>
        </Link>

        <article className={styles.qualityCard}>
          <header>
            <div>
              <span>QUALITY WINDOW · 30D</span>
              <h2>可信通过</h2>
            </div>
            <CircleCheck size={18} />
          </header>
          <strong>
            {metricLabel(
              insight.data?.current.trustedPassRate.percentage ?? null
            )}
          </strong>
          <span>
            {insight.data?.current.trustedPassRate.sampleStatus ??
              (insight.isPending ? "LOADING" : "UNAVAILABLE")}
          </span>
          <div>
            <i
              style={{
                width: `${
                  insight.data?.current.trustedPassRate.percentage ?? 0
                }%`
              }}
            />
          </div>
          <Link href={`${basePath}/insights`}>
            查看 DatasetCut <ArrowUpRight size={14} />
          </Link>
        </article>

        <article className={styles.riskCard}>
          <CircleAlert size={20} />
          <span>RISK LENS</span>
          {risk ? (
            <>
              <h2>{risk.taskPlanName}</h2>
              <p>
                {risk.gateDecision} · {risk.reasonCount} Gate reasons
              </p>
              <Link href={`${basePath}/results?runId=${risk.taskRunId}`}>
                进入任务结果 <ArrowRight size={14} />
              </Link>
            </>
          ) : (
            <>
              <h2>当前窗口无 Active Risk</h2>
              <p>仅表示 Insight API 未返回非通过 Gate 信号。</p>
            </>
          )}
        </article>
      </section>

      <section className={styles.journeyStrip}>
        <header>
          <span>最近运行事实</span>
          <strong>{runs.data?.length ?? "—"}</strong>
        </header>
        {(runs.data ?? []).slice(0, 4).map((run) => (
          <Link
            href={`${basePath}/${run.lifecycle === "CLOSED" ? "results" : "live"}?runId=${run.id}`}
            key={run.id}
          >
            <span>RUN-{shortId(run.id)}</span>
            <strong>{run.lifecycle}</strong>
            <small>{run.quality}</small>
            <b>{run.triggerSource}</b>
            {run.lifecycle !== "CLOSED" ? (
              <Play size={14} />
            ) : (
              <CircleCheck size={14} />
            )}
          </Link>
        ))}
        {!latestRun ? <p>创建第一条 TaskRun 后，最近旅程会出现在这里。</p> : null}
      </section>
    </div>
  );
}
