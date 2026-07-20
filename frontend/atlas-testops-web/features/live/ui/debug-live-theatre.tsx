"use client";

import {
  Activity,
  ArrowLeft,
  BrainCircuit,
  Camera,
  Check,
  ChevronDown,
  CircleAlert,
  CircleStop,
  Code2,
  ExternalLink,
  Eye,
  Fingerprint,
  MonitorDot,
  Radio,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  TestTube2,
  X
} from "lucide-react";
import Image from "next/image";
import Link from "next/link";
import {
  useEffect,
  useMemo,
  useState
} from "react";

import { useSessionQuery } from "@/features/auth/api/auth-queries";
import {
  useCancelDebugRunMutation,
  useCaseCatalogQuery
} from "@/features/case/api/case-queries";
import { ApiProblemError } from "@/shared/api/problem";
import { createRequestId } from "@/shared/api/request-id";
import { ErrorState } from "@/shared/ui/feedback/error-state";
import { LoadingState } from "@/shared/ui/feedback/loading-state";

import {
  useDebugEvidenceQuery,
  useDebugLiveFrameQuery,
  useDebugLiveSnapshotQuery,
  useDebugLiveStream,
  useDebugRunEventsQuery,
  useDebugRunQuery,
  useEvidenceArtifactMutation
} from "../api/live-queries";
import type {
  DebugLiveEventViewModel,
  EvidenceArtifactViewModel
} from "../model/live";
import styles from "./live-page.module.css";

const RUN_OPERATORS = new Set([
  "ORG_ADMIN",
  "PROJECT_ADMIN",
  "RUN_OPERATOR",
  "CASE_AUTHOR"
]);

const DATE_FORMAT = new Intl.DateTimeFormat("zh-CN", {
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit"
});

const EVENT_LABELS: Record<string, string> = {
  "debug_run.requested": "已创建调试运行",
  "debug_run.execution_bound": "已绑定浏览器与执行策略",
  "debug_run.ready": "浏览器运行时已就绪",
  "debug_run.started": "开始执行冻结步骤",
  "debug_run.finalizing": "正在封存证据",
  "debug_run.terminated": "运行结束",
  "debug_run.browser.execution.started": "浏览器执行开始",
  "debug_run.browser.node.started": "开始执行当前步骤",
  "debug_run.browser.observation.captured": "已读取页面可访问结构",
  "debug_run.browser.planner.completed": "已完成目标选择",
  "debug_run.browser.action.proposed": "已生成受限浏览器动作",
  "debug_run.browser.policy.decided": "安全策略已裁决动作",
  "debug_run.browser.action.executed": "浏览器动作已执行",
  "debug_run.browser.artifact.captured": "已捕获证据",
  "debug_run.browser.assertion.evaluated": "断言已验证",
  "debug_run.browser.node.completed": "当前步骤已完成",
  "debug_run.browser.execution.blocked": "浏览器执行被阻断",
  "debug_run.browser.execution.completed": "浏览器执行已完成"
};

type ArtifactPreview = {
  url: string;
  artifact: EvidenceArtifactViewModel;
};

function shortId(value: string): string {
  return value.slice(0, 8).toUpperCase();
}

function shortDigest(value: string): string {
  return (value.split(":").at(-1) ?? value).slice(0, 8).toUpperCase();
}

function problemMessage(error: unknown): string | null {
  if (error instanceof ApiProblemError) return error.problem.detail;
  return error instanceof Error ? error.message : null;
}

function eventFact(
  event: DebugLiveEventViewModel | null | undefined,
  key: string
): string | null {
  const value = event?.data?.[key];
  if (
    typeof value === "string" ||
    typeof value === "number" ||
    typeof value === "boolean"
  ) {
    return String(value);
  }
  return null;
}

function eventSummary(event: DebugLiveEventViewModel | null): string {
  if (!event) return "等待第一条受信运行事实";
  const action = eventFact(event, "action");
  if (event.type === "debug_run.browser.action.executed") {
    if (action === "open_route") return "百度首页已经打开";
    if (action === "enter_text") return "搜索关键词已经填入搜索框";
    if (action === "keypress") return "已提交搜索，正在等待结果页面";
    if (action === "capture_view") return "结果页面截图已经捕获";
  }
  if (event.type === "debug_run.browser.policy.decided") {
    return eventFact(event, "decision") === "ALLOW"
      ? "安全策略允许执行这个动作"
      : "安全策略阻止了这个动作";
  }
  if (event.type === "debug_run.browser.planner.completed") {
    const mode = eventFact(event, "planningMode");
    const status = eventFact(event, "status");
    if (mode === "OPENAI_RESPONSES" && status === "RESOLVED") {
      return "OpenAI 已选择搜索框，接下来仍需通过 Policy Gate";
    }
    if (mode === "OPENAI_RESPONSES" && status === "FALLBACK") {
      return "外部模型调用失败，已安全回退到确定性目标选择";
    }
    return "使用确定性规则识别了搜索框；未调用外部模型";
  }
  return (
    eventFact(event, "safeSummary") ??
    EVENT_LABELS[event.type] ??
    event.type
  );
}

function mergeDebugEvents(
  events: DebugLiveEventViewModel[],
  latestEvent: DebugLiveEventViewModel | null | undefined
): DebugLiveEventViewModel[] {
  const byId = new Map(events.map((event) => [event.id, event]));
  if (latestEvent) byId.set(latestEvent.id, latestEvent);
  return [...byId.values()].sort((left, right) => left.seq - right.seq);
}

function phaseState(
  events: DebugLiveEventViewModel[],
  needle: string
): "done" | "current" | "waiting" {
  const index = events.findIndex((event) => event.type.includes(needle));
  if (index < 0) return "waiting";
  return index === events.length - 1 ? "current" : "done";
}

export function DebugLiveTheatre({
  projectId,
  debugRunId,
  requestedCaseId
}: Readonly<{
  projectId: string;
  debugRunId: string;
  requestedCaseId: string | null;
}>) {
  const session = useSessionQuery();
  const catalog = useCaseCatalogQuery(projectId);
  const snapshot = useDebugLiveSnapshotQuery(debugRunId);
  const frozenRun = useDebugRunQuery(debugRunId);
  const eventsQuery = useDebugRunEventsQuery(debugRunId);
  const resolvedCaseId =
    snapshot.data?.run.testCaseId ??
    frozenRun.data?.testCaseId ??
    requestedCaseId;
  const cancelRun = useCancelDebugRunMutation(resolvedCaseId ?? "unknown");
  const running =
    snapshot.data?.run.lifecycle !== undefined &&
    snapshot.data.run.lifecycle !== "TERMINATED";
  const streamStatus = useDebugLiveStream(debugRunId, running);
  const liveFrame = useDebugLiveFrameQuery(debugRunId, running);
  const evidence = useDebugEvidenceQuery(
    debugRunId,
    snapshot.data?.run.lifecycle === "TERMINATED"
  );
  const readArtifact = useEvidenceArtifactMutation();
  const [preview, setPreview] = useState<ArtifactPreview | null>(null);
  const [clock, setClock] = useState(() => Date.now());
  const frameUrl = useMemo(
    () =>
      liveFrame.data?.blob
        ? URL.createObjectURL(liveFrame.data.blob)
        : null,
    [liveFrame.data]
  );

  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => setClock(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, [running]);

  useEffect(() => {
    return () => {
      if (frameUrl) URL.revokeObjectURL(frameUrl);
    };
  }, [frameUrl]);

  useEffect(
    () => () => {
      if (preview) URL.revokeObjectURL(preview.url);
    },
    [preview]
  );

  const events = useMemo(
    () =>
      mergeDebugEvents(
        eventsQuery.data?.items ?? [],
        snapshot.data?.latestEvent
      ),
    [eventsQuery.data?.items, snapshot.data?.latestEvent]
  );
  const activeEvent = events.at(-1) ?? snapshot.data?.latestEvent ?? null;
  const plannerEvent = [...events]
    .reverse()
    .find((event) => event.type === "debug_run.browser.planner.completed");
  const boundEvent = [...events]
    .reverse()
    .find((event) => event.type === "debug_run.execution_bound");
  const completedNodeIds = new Set(
    events
      .filter((event) => event.type === "debug_run.browser.node.completed")
      .map((event) => eventFact(event, "nodeId"))
      .filter((nodeId): nodeId is string => Boolean(nodeId))
  );
  const activeNodeId =
    [...events]
      .reverse()
      .map((event) => eventFact(event, "nodeId"))
      .find((nodeId) => nodeId && !completedNodeIds.has(nodeId)) ?? null;
  const caseItem = catalog.data?.find((item) => item.id === resolvedCaseId);
  const run = snapshot.data?.run;
  const detail = frozenRun.data;
  const screenshotArtifacts =
    evidence.data?.artifacts.filter(
      (artifact) => artifact.kind === "SCREENSHOT"
    ) ?? [];
  const latestAssertion = evidence.data?.assertions.at(-1) ?? null;
  const canOperate =
    session.data?.roles.some((role) => RUN_OPERATORS.has(role)) ?? false;
  const pageError =
    snapshot.error ??
    frozenRun.error ??
    eventsQuery.error ??
    catalog.error ??
    evidence.error ??
    cancelRun.error ??
    readArtifact.error ??
    liveFrame.error;
  const errorMessage = problemMessage(pageError);

  async function refreshFacts() {
    await Promise.all([
      snapshot.refetch(),
      frozenRun.refetch(),
      eventsQuery.refetch(),
      liveFrame.refetch(),
      run?.lifecycle === "TERMINATED" ? evidence.refetch() : Promise.resolve()
    ]);
  }

  async function requestCancel() {
    if (!run || !resolvedCaseId) return;
    try {
      await cancelRun.mutateAsync({
        runId: debugRunId,
        revision: run.revision,
        command: {
          clientMutationId: `cancel-debug-${createRequestId()}`,
          reason: "Canceled from Atlas Debug Test Theatre."
        }
      });
      await refreshFacts();
    } catch {
      // Mutation state renders the authoritative Problem Details.
    }
  }

  async function openArtifact(artifact: EvidenceArtifactViewModel) {
    try {
      const blob = await readArtifact.mutateAsync({
        debugRunId,
        artifactId: artifact.id,
        purpose: "INLINE"
      });
      if (preview) URL.revokeObjectURL(preview.url);
      setPreview({
        artifact,
        url: URL.createObjectURL(blob)
      });
    } catch {
      // Mutation state renders the authoritative Problem Details.
    }
  }

  if (snapshot.isPending || frozenRun.isPending) {
    return <LoadingState label="正在打开实时调试工作台" />;
  }
  if (snapshot.isError || frozenRun.isError || !run || !detail) {
    return (
      <ErrorState
        detail={
          problemMessage(snapshot.error ?? frozenRun.error) ??
          "Atlas API 未返回完整 DebugRun 现场。"
        }
        onRetry={() => void refreshFacts()}
      />
    );
  }

  const elapsedMs = Math.max(
    0,
    (run.completedAt?.getTime() ?? clock) -
      (run.startedAt?.getTime() ?? run.executionDeadline.getTime())
  );
  const elapsedLabel =
    elapsedMs >= 60_000
      ? `${Math.floor(elapsedMs / 60_000)}m ${Math.floor(
          (elapsedMs % 60_000) / 1_000
        )}s`
      : `${Math.floor(elapsedMs / 1_000)}s`;
  const plannerMode = eventFact(plannerEvent, "planningMode") ?? "NOT_RECORDED";
  const externalCall = eventFact(plannerEvent, "externalCall") === "true";
  const plannerStatus = eventFact(plannerEvent, "status") ?? "等待运行事实";
  const browserSource = preview?.url ?? frameUrl;
  const browserSourceKind = preview
    ? "EVIDENCE"
    : frameUrl
      ? running
        ? "LIVE"
        : "LAST"
      : "WAITING";
  const streamLabel = running
    ? streamStatus.toUpperCase()
    : "SEALED";
  const browserLocation = events.some(
    (event) =>
      event.type === "debug_run.browser.action.executed" &&
      eventFact(event, "action") === "keypress"
  )
    ? `baidu.com/s?wd=${encodeURIComponent(detail.searchKeyword ?? "")}`
    : "baidu.com";
  const phases = [
    ["Observe", "observation.captured"],
    ["Decide", "planner.completed"],
    ["Act", "action.executed"],
    ["Verify", "assertion.evaluated"],
    ["Evidence", "artifact.captured"]
  ] as const;

  return (
    <div className={styles.page}>
      <section className={styles.runtimeWorkbench}>
        <header className={styles.runtimeWorkbenchHeader}>
          <div>
            <p>
              <Activity size={13} /> REAL-TIME DEBUG · DRAFT R
              {detail.semanticRevision}
            </p>
            <h1>
              {caseItem?.name ??
                `百度搜索 ${detail.searchKeyword ?? "冻结关键词"}`}
            </h1>
            <span>
              {caseItem?.key ?? `CASE-${shortId(run.testCaseId)}`} · 当前草稿的隔离调试
            </span>
          </div>
          <dl>
            <div>
              <dt>状态</dt>
              <dd data-outcome={run.outcome}>
                <Radio size={10} /> {run.lifecycle} · {run.outcome}
              </dd>
            </div>
            <div>
              <dt>耗时</dt>
              <dd>{elapsedLabel}</dd>
            </div>
            <div>
              <dt>环境</dt>
              <dd>ENV-{shortId(run.environmentId)}</dd>
            </div>
            <div>
              <dt>Run</dt>
              <dd>{shortId(run.id)}</dd>
            </div>
          </dl>
        </header>

        <div className={styles.runtimeWorkbenchGrid}>
          <aside className={styles.readableSteps}>
            <header>
              <div>
                <span>执行步骤</span>
                <strong>
                  {completedNodeIds.size}/{detail.planNodes.length}
                </strong>
              </div>
              <p>按冻结计划顺序执行</p>
            </header>
            <ol>
              {detail.planNodes.map((node, index) => {
                const state = completedNodeIds.has(node.id)
                  ? "done"
                  : activeNodeId === node.id
                    ? "current"
                    : "waiting";
                return (
                  <li data-state={state} key={node.id}>
                    <i>{state === "done" ? <Check size={12} /> : index + 1}</i>
                    <div>
                      <strong>{node.title}</strong>
                      <span>{node.description}</span>
                      <small>{node.versionRef}</small>
                    </div>
                    <b />
                  </li>
                );
              })}
            </ol>
            <footer>
              <Fingerprint size={13} />
              PLAN {shortDigest(detail.planDigest)} · 不可变
            </footer>
          </aside>

          <main className={styles.browserStage}>
            <header className={styles.browserChrome}>
              <div>
                <i />
                <i />
                <i />
              </div>
              <p>
                <ShieldCheck size={12} />
                {browserLocation}
              </p>
              <em data-state={browserSourceKind}>
                <MonitorDot size={12} />
                {browserSourceKind === "LIVE"
                  ? `LIVE · FRAME ${liveFrame.data?.frameRevision ?? 0}`
                  : browserSourceKind === "EVIDENCE"
                    ? "VERIFIED EVIDENCE"
                    : browserSourceKind === "LAST"
                      ? `最后一帧 · FRAME ${liveFrame.data?.frameRevision ?? 0}`
                      : "等待首帧"}
              </em>
            </header>
            <div className={styles.browserViewport}>
              {browserSource ? (
                <Image
                  alt={
                    preview
                      ? "已验证的浏览器证据截图"
                      : "实时浏览器页面"
                  }
                  fill
                  priority
                  sizes="(max-width: 900px) 100vw, 60vw"
                  src={browserSource}
                  unoptimized
                />
              ) : (
                <div className={styles.browserWaiting}>
                  <MonitorDot size={34} />
                  <strong>正在等待 Browser Runtime 发布首帧</strong>
                  <p>
                    页面动作开始后，这里会自动显示经过遮罩的真实浏览器视口。
                  </p>
                </div>
              )}
              <div className={styles.browserStatusOverlay}>
                <span>
                  <Radio size={9} /> {running ? "LIVE" : "TERMINAL"}
                </span>
                <strong>{eventSummary(activeEvent)}</strong>
                <small>
                  {activeEvent
                    ? `${DATE_FORMAT.format(activeEvent.occurredAt)} · EVENT #${activeEvent.seq}`
                    : "等待事件"}
                </small>
              </div>
              {preview ? (
                <button
                  className={styles.closeBrowserPreview}
                  aria-label="关闭 Evidence 预览"
                  type="button"
                  onClick={() => setPreview(null)}
                >
                  <X size={14} />
                </button>
              ) : null}
            </div>
          </main>

          <aside className={styles.runtimeInspector}>
            <article className={styles.nowCard}>
              <header>
                <span>
                  <Sparkles size={14} /> 此刻正在做什么
                </span>
                <em data-state={streamStatus}>{streamLabel}</em>
              </header>
              <h2>{eventSummary(activeEvent)}</h2>
              <p>
                {eventFact(activeEvent, "nodeId")
                  ? `步骤 ${eventFact(activeEvent, "nodeId")}`
                  : "控制面运行事实"}
              </p>
              <dl>
                <div>
                  <dt>动作</dt>
                  <dd>{eventFact(activeEvent, "action") ?? "—"}</dd>
                </div>
                <div>
                  <dt>裁决</dt>
                  <dd>{eventFact(activeEvent, "decision") ?? "—"}</dd>
                </div>
                <div>
                  <dt>结果</dt>
                  <dd>{eventFact(activeEvent, "status") ?? run.outcome}</dd>
                </div>
              </dl>
            </article>

            <article className={styles.aiRuntimeCard}>
              <header>
                <span>
                  <BrainCircuit size={15} /> AI Runtime
                </span>
                <em data-external={externalCall}>
                  {externalCall ? "EXTERNAL CALL" : "NO EXTERNAL CALL"}
                </em>
              </header>
              <div className={styles.aiRuntimeMode}>
                <strong>{plannerMode}</strong>
                <span>{plannerStatus}</span>
              </div>
              <dl>
                <div>
                  <dt>Provider / Model</dt>
                  <dd>
                    {eventFact(plannerEvent, "provider") ?? "NONE"} /{" "}
                    {eventFact(plannerEvent, "model") ?? "NONE"}
                  </dd>
                </div>
                <div>
                  <dt>模型 Profile</dt>
                  <dd>
                    {eventFact(plannerEvent, "modelProfileRef") ??
                      eventFact(boundEvent, "modelProfileRef") ??
                      "未记录"}
                  </dd>
                </div>
                <div>
                  <dt>Prompt Bundle</dt>
                  <dd>
                    {eventFact(plannerEvent, "promptBundleRef") ?? "未记录"}
                    {!externalCall ? " · 未外部调用" : ""}
                  </dd>
                </div>
                <div>
                  <dt>耗时 / 用量</dt>
                  <dd>
                    {eventFact(plannerEvent, "latencyMs") ?? 0} ms ·{" "}
                    {eventFact(plannerEvent, "inputUnits") ?? 0}/
                    {eventFact(plannerEvent, "outputUnits") ?? 0}
                  </dd>
                </div>
              </dl>
              <p>
                <ShieldCheck size={12} />
                这里只展示调用事实与摘要，不展示或伪造模型思维链；任何动作仍由
                Policy Gate 决定。
              </p>
            </article>

            {errorMessage ? (
              <p className={styles.inlineError} role="alert">
                <CircleAlert size={13} /> {errorMessage}
              </p>
            ) : null}
          </aside>
        </div>

        <div className={styles.runtimeTimeline}>
          {phases.map(([label, needle], index) => {
            const state = phaseState(events, needle);
            return (
              <div data-state={state} key={label}>
                <i>{state === "done" ? <Check size={10} /> : index + 1}</i>
                <span>{label}</span>
                <small>
                  {
                    {
                      Observe: "读取页面结构",
                      Decide: "选择目标",
                      Act: "执行动作",
                      Verify: "求值断言",
                      Evidence: "封存证据"
                    }[label]
                  }
                </small>
              </div>
            );
          })}
        </div>

        <section className={styles.runtimeOutcome}>
          <header>
            <div>
              <TestTube2 size={17} />
              <span>运行结果</span>
              <strong data-outcome={run.outcome}>{run.outcome}</strong>
            </div>
            <p>
              {latestAssertion?.summary ??
                (running
                  ? "运行尚未结束，最终结果只由 Oracle 与 Evidence 决定。"
                  : "DebugRun 已到达终态。")}
            </p>
          </header>
          <div className={styles.outcomeMetrics}>
            <article>
              <span>断言</span>
              <strong>
                {evidence.data
                  ? `${evidence.data.passedAssertions} 通过 / ${evidence.data.failedAssertions} 失败`
                  : "等待求值"}
              </strong>
              <small>
                {latestAssertion
                  ? `${latestAssertion.strength} · ${latestAssertion.durationMs} ms`
                  : "Oracle"}
              </small>
            </article>
            <article>
              <span>证据</span>
              <strong>
                {evidence.data
                  ? `${evidence.data.completeness} · ${evidence.data.integrity}`
                  : "等待封存"}
              </strong>
              <small>
                {evidence.data
                  ? `${evidence.data.artifacts.length} 个 Artifact · ${evidence.data.eventCount} 条 Browser Report`
                  : "Evidence Service"}
              </small>
            </article>
            <article>
              <span>资源收尾</span>
              <strong>{running ? "等待运行结束" : "控制面收尾已执行"}</strong>
              <small>Fixture · Session · Lease</small>
            </article>
          </div>
          <div className={styles.evidenceActions}>
            {screenshotArtifacts.map((artifact, index) => (
              <button
                type="button"
                disabled={readArtifact.isPending}
                onClick={() => void openArtifact(artifact)}
                key={artifact.id}
              >
                <Camera size={14} />
                截图 {index + 1}
                <span>{Math.ceil(artifact.sizeBytes / 1024)} KB</span>
              </button>
            ))}
            {evidence.data ? (
              <span>
                <Fingerprint size={12} />
                MANIFEST {shortDigest(evidence.data.contentDigest)}
              </span>
            ) : null}
          </div>
        </section>

        <details className={styles.technicalEvents}>
          <summary>
            <span>
              <Code2 size={14} /> 技术事件与完整字段
            </span>
            <em>
              {events.length} EVENTS <ChevronDown size={13} />
            </em>
          </summary>
          <div>
            {events.map((event) => (
              <article key={event.id}>
                <header>
                  <strong>#{event.seq}</strong>
                  <span>{EVENT_LABELS[event.type] ?? event.type}</span>
                  <time>{DATE_FORMAT.format(event.occurredAt)}</time>
                </header>
                <p>{eventSummary(event)}</p>
                <pre>{JSON.stringify(event.data, null, 2)}</pre>
              </article>
            ))}
          </div>
        </details>

        <footer className={styles.runtimeControls}>
          <Link href={`/projects/${projectId}/cases?caseId=${run.testCaseId}`}>
            <ArrowLeft size={14} /> 返回用例
          </Link>
          <button
            aria-label="刷新 DebugRun 事实"
            type="button"
            onClick={() => void refreshFacts()}
          >
            <RefreshCw size={14} /> 刷新事实
          </button>
          <span>
            <Eye size={13} /> 只读观察 · {streamLabel}
          </span>
          <i />
          {run.lifecycle !== "TERMINATED" ? (
            <button
              className={styles.cancelRuntime}
              type="button"
              onClick={() => void requestCancel()}
              disabled={!canOperate || cancelRun.isPending}
            >
              <CircleStop size={14} /> 取消 Debug
            </button>
          ) : (
            <span className={styles.runtimeTerminal}>
              <Check size={13} /> {run.outcome}
            </span>
          )}
          <Link href={`/projects/${projectId}/cases?caseId=${run.testCaseId}`}>
            {run.outcome === "PASSED" ? "返回并发布" : "检查用例"}
            <ExternalLink size={13} />
          </Link>
        </footer>
      </section>
    </div>
  );
}
