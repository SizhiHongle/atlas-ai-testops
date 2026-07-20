"use client";

import {
  ArrowLeft,
  ArrowRight,
  BadgeCheck,
  Check,
  CircleAlert,
  CircleCheck,
  Clock3,
  Component,
  Fingerprint,
  Globe2,
  Play,
  Plus,
  Radio,
  Rocket,
  ShieldCheck,
  Sparkles,
  Terminal
} from "lucide-react";
import { useRouter } from "next/navigation";
import {
  useMemo,
  useState
} from "react";

import type { IdentityWalletViewModel } from "@/features/identity/model/identity";
import { ApiProblemError } from "@/shared/api/problem";
import { createRequestId } from "@/shared/api/request-id";
import { canonicalDigest } from "@/shared/crypto/canonical-digest";

import {
  useCreateTaskScheduleMutation,
  useStartTaskRunMutation
} from "../api/task-queries";
import type {
  TaskAssemblyCatalogViewModel,
  TaskControlCatalogViewModel,
  TaskPlanVersionViewModel
} from "../model/task";
import styles from "./task-page.module.css";

type TriggerKind = "manual" | "schedule" | "external";

type RetryPolicyInput = {
  infraRetryAttempts: number;
  maxTotalInfraRetries: number;
  initialBackoffSeconds: number;
  maximumBackoffSeconds: number;
  jitterPercent: number;
};

const DEFAULT_RETRY_POLICY: RetryPolicyInput = {
  infraRetryAttempts: 1,
  maxTotalInfraRetries: 8,
  initialBackoffSeconds: 2,
  maximumBackoffSeconds: 30,
  jitterPercent: 10
};

const SCHEDULE_KEY_PATTERN =
  /^[a-z][a-z0-9]*(?:[._-][a-z0-9]+){0,7}$/;

function mutationMessage(error: Error | null): string | null {
  if (!error) return null;
  return error instanceof ApiProblemError
    ? error.problem.detail
    : error.message;
}

function findVersion(
  versions: TaskPlanVersionViewModel[],
  versionId: string
): TaskPlanVersionViewModel | null {
  return versions.find((version) => version.id === versionId) ?? null;
}

export function TaskBuilder({
  projectId,
  control,
  assembly,
  identity,
  canOperate,
  onBack,
  onCreatePlan
}: Readonly<{
  projectId: string;
  control: TaskControlCatalogViewModel;
  assembly: TaskAssemblyCatalogViewModel;
  identity: IdentityWalletViewModel | null;
  canOperate: boolean;
  onBack: () => void;
  onCreatePlan: () => void;
}>) {
  const router = useRouter();
  const firstPlan = control.plans[0] ?? null;
  const initialVersions = control.versions.filter(
    (version) => version.taskPlanId === firstPlan?.id
  );
  const initialVersion = initialVersions[0] ?? null;
  const initialSchedule = control.schedules.find(
    (schedule) => schedule.taskPlanVersionId === initialVersion?.id
  );
  const [planId, setPlanId] = useState(firstPlan?.id ?? "");
  const [versionId, setVersionId] = useState(initialVersion?.id ?? "");
  const [selectedCaseVersionIds, setSelectedCaseVersionIds] = useState(
    initialVersion?.pinnedCaseVersionIds ??
      assembly.caseVersions.slice(0, 3).map((version) => version.id)
  );
  const [selectedEnvironmentIds, setSelectedEnvironmentIds] = useState(
    initialVersion?.environmentIds ??
      assembly.environments.slice(0, 1).map((environment) => environment.id)
  );
  const [concurrency, setConcurrency] = useState(8);
  const [retryPolicy, setRetryPolicy] = useState<RetryPolicyInput>(
    initialSchedule
      ? {
          infraRetryAttempts:
            initialSchedule.retryPolicy.infraRetryAttempts,
          maxTotalInfraRetries:
            initialSchedule.retryPolicy.maxTotalInfraRetries,
          initialBackoffSeconds:
            initialSchedule.retryPolicy.initialBackoffSeconds,
          maximumBackoffSeconds:
            initialSchedule.retryPolicy.maximumBackoffSeconds,
          jitterPercent: initialSchedule.retryPolicy.jitterPercent
        }
      : DEFAULT_RETRY_POLICY
  );
  const [trigger, setTrigger] = useState<TriggerKind>("manual");
  const [scheduleName, setScheduleName] = useState(
    firstPlan ? `${firstPlan.name}每日调度` : "每日回归"
  );
  const [scheduleKey, setScheduleKey] = useState(
    firstPlan ? `${firstPlan.key}.daily` : "daily.regression"
  );
  const [scheduleHour, setScheduleHour] = useState(21);
  const [scheduleMinute, setScheduleMinute] = useState(30);
  const [timeZoneName, setTimeZoneName] = useState("Asia/Shanghai");
  const [feedback, setFeedback] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);
  const startRun = useStartTaskRunMutation(projectId);
  const createSchedule = useCreateTaskScheduleMutation(projectId);

  const plan =
    control.plans.find((candidate) => candidate.id === planId) ?? firstPlan;
  const planVersions = useMemo(
    () =>
      control.versions.filter(
        (version) => version.taskPlanId === plan?.id
      ),
    [control.versions, plan?.id]
  );
  const selectedVersion = findVersion(planVersions, versionId);
  const selectedSchedules = control.schedules.filter(
    (schedule) => schedule.taskPlanVersionId === selectedVersion?.id
  );
  const selectedCaseVersions = assembly.caseVersions.filter((version) =>
    selectedCaseVersionIds.includes(version.id)
  );
  const selectedRoles = [
    ...new Set(
      selectedCaseVersions
        .map((version) => version.roleKey)
      .filter((role): role is string => Boolean(role))
    )
  ];
  const exactVersionSelected = Boolean(selectedVersion);
  const plannedExecutions = selectedVersion?.matrixSize ?? 0;
  const profileCatalogReady = false;
  const requestError =
    feedback ??
    mutationMessage(startRun.error) ??
    mutationMessage(createSchedule.error);

  function applyVersion(nextVersion: TaskPlanVersionViewModel | null) {
    setVersionId(nextVersion?.id ?? "");
    if (!nextVersion) return;
    setSelectedCaseVersionIds(nextVersion.pinnedCaseVersionIds);
    setSelectedEnvironmentIds(nextVersion.environmentIds);
    const schedule = control.schedules.find(
      (candidate) => candidate.taskPlanVersionId === nextVersion.id
    );
    if (schedule) {
      setRetryPolicy({
        infraRetryAttempts: schedule.retryPolicy.infraRetryAttempts,
        maxTotalInfraRetries: schedule.retryPolicy.maxTotalInfraRetries,
        initialBackoffSeconds: schedule.retryPolicy.initialBackoffSeconds,
        maximumBackoffSeconds: schedule.retryPolicy.maximumBackoffSeconds,
        jitterPercent: schedule.retryPolicy.jitterPercent
      });
    }
    setFeedback(null);
    setSuccessMessage(null);
  }

  function selectPlan(nextPlanId: string) {
    setPlanId(nextPlanId);
    const nextVersion =
      control.versions.find(
        (version) => version.taskPlanId === nextPlanId
      ) ?? null;
    const nextPlan = control.plans.find(
      (candidate) => candidate.id === nextPlanId
    );
    setScheduleName(
      nextPlan ? `${nextPlan.name}每日调度` : "每日回归"
    );
    setScheduleKey(
      nextPlan ? `${nextPlan.key}.daily` : "daily.regression"
    );
    applyVersion(nextVersion);
  }

  function detachExactVersion() {
    if (versionId) setVersionId("");
    setFeedback(null);
    setSuccessMessage(null);
  }

  function toggleCaseVersion(caseVersionId: string) {
    detachExactVersion();
    setSelectedCaseVersionIds((current) =>
      current.includes(caseVersionId)
        ? current.filter((id) => id !== caseVersionId)
        : [...current, caseVersionId]
    );
  }

  function toggleEnvironment(environmentId: string) {
    detachExactVersion();
    setSelectedEnvironmentIds((current) =>
      current.includes(environmentId)
        ? current.filter((id) => id !== environmentId)
        : [...current, environmentId]
    );
  }

  function updateRetryPolicy(
    key: keyof RetryPolicyInput,
    value: number
  ) {
    setRetryPolicy((current) => ({ ...current, [key]: value }));
    setFeedback(null);
    setSuccessMessage(null);
  }

  async function buildRetryPolicy() {
    const body = {
      schemaVersion: "atlas.task-retry-policy/0.1" as const,
      ...retryPolicy
    };
    return {
      ...body,
      contentDigest: await canonicalDigest(body)
    };
  }

  async function launchSelectedVersion() {
    if (!selectedVersion) return;
    const frozenRetryPolicy = await buildRetryPolicy();
    if (frozenRetryPolicy.contentDigest !== selectedVersion.retryPolicyDigest) {
      setFeedback(
        "当前失败策略与 TaskPlanVersion 的冻结摘要不一致。请选择已有 Schedule 使用的策略，或按版本发布时的策略调整后再启动。"
      );
      return;
    }
    setFeedback(null);
    setSuccessMessage(null);
    const runId = await startRun.mutateAsync({
      taskPlanVersionId: selectedVersion.id,
      command: {
        clientMutationId: `start-run-${createRequestId()}`,
        iterationId: null,
        retryPolicy: frozenRetryPolicy
      }
    });
    router.push(`/projects/${projectId}/live?runId=${runId}`);
  }

  async function scheduleSelectedVersion() {
    if (!selectedVersion) return;
    if (!scheduleName.trim()) {
      setFeedback("请填写 Schedule 名称。");
      return;
    }
    if (!SCHEDULE_KEY_PATTERN.test(scheduleKey)) {
      setFeedback("Schedule Key 需使用小写字母、数字及 . _ - 分隔。");
      return;
    }
    const frozenRetryPolicy = await buildRetryPolicy();
    if (frozenRetryPolicy.contentDigest !== selectedVersion.retryPolicyDigest) {
      setFeedback(
        "当前失败策略与 TaskPlanVersion 的冻结摘要不一致，不能创建会漂移的 Schedule。"
      );
      return;
    }
    const scheduleId = await createSchedule.mutateAsync({
      taskPlanVersionId: selectedVersion.id,
      command: {
        scheduleKey,
        name: scheduleName.trim(),
        calendar: {
          schemaVersion: "atlas.task-schedule-calendar/0.1",
          minutes: [scheduleMinute],
          hours: [scheduleHour],
          daysOfMonth: [],
          months: [],
          isoDaysOfWeek: []
        },
        timeZoneName,
        overlapPolicy: "QUEUE_ONE",
        catchupPolicy: "RUN_ONCE",
        catchupWindowSeconds: 3600,
        jitterSeconds: 0,
        iterationId: null,
        retryPolicy: frozenRetryPolicy,
        clientMutationId: `create-schedule-${createRequestId()}`
      }
    });
    setFeedback(null);
    setSuccessMessage(
      `Schedule ${scheduleId.slice(0, 8).toUpperCase()} 已创建。`
    );
  }

  async function submitAssembly() {
    if (!canOperate) return;
    try {
      if (trigger === "manual") {
        await launchSelectedVersion();
        return;
      }
      if (trigger === "schedule") {
        await scheduleSelectedVersion();
      }
    } catch {
      // Mutation state renders the backend problem.
    }
  }

  return (
    <>
      <div className={styles.builderSteps}>
        {[
          "测试范围",
          "执行矩阵",
          "资源策略",
          "失败策略",
          "触发方式"
        ].map((step, index) => (
          <span
            className={index < 2 ? styles.activeStep : ""}
            key={step}
          >
            <i>{index + 1}</i>
            {step}
          </span>
        ))}
        <button type="button" onClick={onBack}>
          <ArrowLeft size={13} /> 返回任务中心
        </button>
      </div>

      <div className={styles.builderPlanBar}>
        <div>
          <span>TASK PLAN</span>
          <select
            aria-label="任务方案"
            value={plan?.id ?? ""}
            onChange={(event) => selectPlan(event.target.value)}
          >
            {control.plans.length ? (
              control.plans.map((candidate) => (
                <option value={candidate.id} key={candidate.id}>
                  {candidate.name} · {candidate.key}
                </option>
              ))
            ) : (
              <option value="">尚无 TaskPlan</option>
            )}
          </select>
        </div>
        <div>
          <span>IMMUTABLE VERSION</span>
          <select
            aria-label="不可变任务版本"
            value={selectedVersion?.id ?? ""}
            onChange={(event) =>
              applyVersion(findVersion(planVersions, event.target.value))
            }
            disabled={!planVersions.length}
          >
            {!planVersions.length ? (
              <option value="">等待首次发布</option>
            ) : null}
            {planVersions.map((version) => (
              <option value={version.id} key={version.id}>
                {version.versionRef}
              </option>
            ))}
            {versionId === "" && planVersions.length ? (
              <option value="">未发布的新装配</option>
            ) : null}
          </select>
        </div>
        <button type="button" onClick={onCreatePlan} disabled={!canOperate}>
          <Plus size={14} /> 新建 TaskPlan
        </button>
      </div>

      <div className={styles.taskBuilder}>
        <div className={styles.builderConfig}>
          <section className={`${styles.builderPanel} ${styles.scopePanel}`}>
            <header>
              <div>
                <span>01 / VERSION VAULT</span>
                <h2>选择已发布用例版本</h2>
              </div>
              <em data-tone="good">
                {selectedCaseVersionIds.length} PINNED
              </em>
            </header>
            <p>
              Task 只保存不可变 CaseVersion ID；草稿与调试结果不会进入正式批量执行。
            </p>
            <div className={styles.versionVaultGrid}>
              {assembly.caseVersions.length ? (
                assembly.caseVersions.map((version) => {
                  const selected = selectedCaseVersionIds.includes(version.id);
                  return (
                    <button
                      type="button"
                      className={selected ? styles.selectedOption : ""}
                      onClick={() => toggleCaseVersion(version.id)}
                      key={version.id}
                    >
                      <i>{selected ? <Check size={12} /> : <Plus size={12} />}</i>
                      <div>
                        <span>
                          {version.caseKey} · {version.roleKey ?? "unbound"}
                        </span>
                        <strong>{version.caseName}</strong>
                        <small>
                          Draft r{version.semanticRevision} ·{" "}
                          {version.publishedAt.toLocaleDateString("zh-CN")}
                        </small>
                      </div>
                      <b>{version.version}</b>
                    </button>
                  );
                })
              ) : (
                <div className={styles.catalogGap}>
                  <CircleAlert size={17} />
                  <div>
                    <strong>还没有已发布 CaseVersion</strong>
                    <small>请先在用例工作台完成调试和发布。</small>
                  </div>
                </div>
              )}
            </div>
            <button
              type="button"
              className={styles.impactSwitch}
              onClick={() => {
                const latestByCase = new Map<string, string>();
                assembly.caseVersions.forEach((version) => {
                  if (!latestByCase.has(version.testCaseId)) {
                    latestByCase.set(version.testCaseId, version.id);
                  }
                });
                detachExactVersion();
                setSelectedCaseVersionIds([...latestByCase.values()]);
              }}
              disabled={!assembly.caseVersions.length}
            >
              <Sparkles size={15} />
              <div>
                <strong>选择每条用例的最新发布版本</strong>
                <small>只选择已发布快照，不越过草稿边界</small>
              </div>
              <i />
            </button>
          </section>

          <section className={`${styles.builderPanel} ${styles.matrixPanel}`}>
            <header>
              <div>
                <span>02 / MATRIX</span>
                <h2>展开执行矩阵</h2>
              </div>
              <em data-tone="violet">实时计算</em>
            </header>
            <div className={styles.matrixOptionGroup}>
              <span>CaseVersion 内置角色 · 已锁定</span>
              <div>
                {selectedRoles.length ? (
                  selectedRoles.map((role) => (
                    <button
                      type="button"
                      className={styles.selectedOption}
                      title="角色由 CaseVersion 冻结，不能在 Task 中覆盖"
                      key={role}
                    >
                      <Fingerprint size={13} />
                      {role}
                      <BadgeCheck size={12} />
                    </button>
                  ))
                ) : (
                  <small>等待选择包含角色绑定的 CaseVersion</small>
                )}
              </div>
            </div>
            <div className={styles.matrixOptionGroup}>
              <span>执行环境</span>
              <div>
                {assembly.environments.map((environment) => (
                  <button
                    type="button"
                    className={
                      selectedEnvironmentIds.includes(environment.id)
                        ? styles.selectedOption
                        : ""
                    }
                    onClick={() => toggleEnvironment(environment.id)}
                    key={environment.id}
                  >
                    <Radio size={13} />
                    {environment.name}
                    <small>{environment.kind}</small>
                  </button>
                ))}
              </div>
            </div>
            <div className={styles.matrixOptionGroup}>
              <span>浏览器 / 身份 / 数据 Profile</span>
              <div>
                <button
                  type="button"
                  disabled
                  title="等待后端开放 Profile Catalog"
                >
                  <Globe2 size={13} /> Profile Catalog 尚未开放
                </button>
              </div>
            </div>
            <div className={styles.contractBoundary}>
              <CircleAlert size={15} />
              <p>
                TaskPlanVersion 发布 API 已存在，但 Profile Catalog
                尚未提供可选择的精确版本；前端不会要求手填 UUID。
              </p>
            </div>
          </section>

          <section className={`${styles.builderPanel} ${styles.policyPanel}`}>
            <header>
              <div>
                <span>03—04 / POLICY</span>
                <h2>资源与失败策略</h2>
              </div>
            </header>
            <div className={styles.policyRow}>
              <span>界面并发预算</span>
              <div>
                {[4, 8, 12].map((value) => (
                  <button
                    type="button"
                    className={concurrency === value ? styles.selectedOption : ""}
                    onClick={() => setConcurrency(value)}
                    key={value}
                  >
                    {value}
                  </button>
                ))}
              </div>
            </div>
            <label className={styles.policyRow}>
              <span>单元基础设施重试</span>
              <input
                aria-label="单元基础设施重试"
                type="number"
                min={0}
                max={4}
                value={retryPolicy.infraRetryAttempts}
                onChange={(event) =>
                  updateRetryPolicy(
                    "infraRetryAttempts",
                    Number(event.target.value)
                  )
                }
              />
            </label>
            <label className={styles.policyRow}>
              <span>任务最大重试总数</span>
              <input
                aria-label="任务最大重试总数"
                type="number"
                min={0}
                max={256}
                value={retryPolicy.maxTotalInfraRetries}
                onChange={(event) =>
                  updateRetryPolicy(
                    "maxTotalInfraRetries",
                    Number(event.target.value)
                  )
                }
              />
            </label>
            <div className={styles.backoffGrid}>
              <label>
                初始退避
                <input
                  type="number"
                  min={1}
                  max={300}
                  value={retryPolicy.initialBackoffSeconds}
                  onChange={(event) =>
                    updateRetryPolicy(
                      "initialBackoffSeconds",
                      Number(event.target.value)
                    )
                  }
                />
              </label>
              <label>
                最大退避
                <input
                  type="number"
                  min={1}
                  max={3600}
                  value={retryPolicy.maximumBackoffSeconds}
                  onChange={(event) =>
                    updateRetryPolicy(
                      "maximumBackoffSeconds",
                      Number(event.target.value)
                    )
                  }
                />
              </label>
              <label>
                抖动 %
                <input
                  type="number"
                  min={0}
                  max={50}
                  value={retryPolicy.jitterPercent}
                  onChange={(event) =>
                    updateRetryPolicy(
                      "jitterPercent",
                      Number(event.target.value)
                    )
                  }
                />
              </label>
            </div>
            <div className={styles.policyNote}>
              <ShieldCheck size={15} />
              <p>
                产品断言失败不会自动重跑；只对明确分类的基础设施故障应用有界重试。
              </p>
            </div>
          </section>

          <section className={`${styles.builderPanel} ${styles.triggerPanel}`}>
            <header>
              <div>
                <span>05 / TRIGGER</span>
                <h2>选择触发方式</h2>
              </div>
            </header>
            <div className={styles.triggerDeck}>
              <button
                type="button"
                className={trigger === "manual" ? styles.selectedOption : ""}
                onClick={() => setTrigger("manual")}
              >
                <Play size={15} />
                <strong>立即执行</strong>
                <small>创建后进入现场</small>
              </button>
              <button
                type="button"
                className={trigger === "schedule" ? styles.selectedOption : ""}
                onClick={() => setTrigger("schedule")}
              >
                <Clock3 size={15} />
                <strong>每日调度</strong>
                <small>数据库权威 Schedule</small>
              </button>
              <button
                type="button"
                className={trigger === "external" ? styles.selectedOption : ""}
                onClick={() => setTrigger("external")}
              >
                <Terminal size={15} />
                <strong>CI / API</strong>
                <small>由外部幂等触发</small>
              </button>
            </div>
            {trigger === "schedule" ? (
              <div className={styles.scheduleFields}>
                <label>
                  名称
                  <input
                    value={scheduleName}
                    onChange={(event) => setScheduleName(event.target.value)}
                  />
                </label>
                <label>
                  Schedule Key
                  <input
                    value={scheduleKey}
                    onChange={(event) => setScheduleKey(event.target.value)}
                  />
                </label>
                <label>
                  时间
                  <span>
                    <input
                      aria-label="调度小时"
                      type="number"
                      min={0}
                      max={23}
                      value={scheduleHour}
                      onChange={(event) =>
                        setScheduleHour(Number(event.target.value))
                      }
                    />
                    <b>:</b>
                    <input
                      aria-label="调度分钟"
                      type="number"
                      min={0}
                      max={59}
                      value={scheduleMinute}
                      onChange={(event) =>
                        setScheduleMinute(Number(event.target.value))
                      }
                    />
                  </span>
                </label>
                <label>
                  IANA 时区
                  <input
                    value={timeZoneName}
                    onChange={(event) => setTimeZoneName(event.target.value)}
                  />
                </label>
              </div>
            ) : null}
            {selectedSchedules.length ? (
              <div className={styles.existingSchedules}>
                <span>EXISTING SCHEDULES</span>
                {selectedSchedules.map((schedule) => (
                  <small key={schedule.id}>
                    <i data-active={schedule.status === "ACTIVE"} />
                    {schedule.name} · {schedule.status} · {schedule.syncStatus}
                  </small>
                ))}
              </div>
            ) : null}
          </section>
        </div>

        <aside className={styles.matrixReactor}>
          <span>MATRIX REACTOR</span>
          <div className={styles.reactorRings}>
            <i />
            <i />
            <i />
            <i />
            <div>
              <Component size={22} />
              <strong>{exactVersionSelected ? plannedExecutions : "—"}</strong>
              <small>EXECUTIONS</small>
            </div>
          </div>
          <code>
            {selectedCaseVersionIds.length} CaseVersion ×{" "}
            {selectedEnvironmentIds.length} 环境
          </code>
          <div className={styles.reactorMetrics}>
            <div>
              <span>精确矩阵</span>
              <strong>
                {exactVersionSelected ? `${plannedExecutions} Units` : "待发布"}
              </strong>
            </div>
            <div>
              <span>角色槽位</span>
              <strong>{selectedRoles.length || "—"}</strong>
            </div>
            <div>
              <span>界面并发</span>
              <strong>{concurrency}</strong>
            </div>
            <div>
              <span>可用账号</span>
              <strong>{identity?.totals.available ?? "—"}</strong>
            </div>
          </div>
          <div
            className={styles.reactorReady}
            data-ready={exactVersionSelected}
          >
            {exactVersionSelected ? (
              <CircleCheck size={15} />
            ) : (
              <CircleAlert size={15} />
            )}
            <div>
              <strong>
                {exactVersionSelected
                  ? `${selectedVersion?.caseCount} 个版本均已冻结`
                  : "新装配尚不能发布"}
              </strong>
              <small>
                {exactVersionSelected
                  ? selectedVersion?.versionRef
                  : "等待 Profile Catalog 提供精确依赖版本"}
              </small>
            </div>
          </div>
          {requestError ? (
            <p className={styles.builderError} role="alert">
              {requestError}
            </p>
          ) : null}
          {successMessage ? (
            <p className={styles.builderSuccess} role="status">
              {successMessage}
            </p>
          ) : null}
          <button
            type="button"
            onClick={() => void submitAssembly()}
            disabled={
              !canOperate ||
              !exactVersionSelected ||
              trigger === "external" ||
              startRun.isPending ||
              createSchedule.isPending
            }
          >
            <Rocket size={16} />
            {startRun.isPending || createSchedule.isPending
              ? "正在提交…"
              : trigger === "manual"
                ? "创建并进入现场"
                : trigger === "schedule"
                  ? "创建调度任务"
                  : "由外部系统触发"}
            <ArrowRight size={15} />
          </button>
          {!exactVersionSelected ? (
            <button
              type="button"
              className={styles.publishBlocked}
              disabled={!profileCatalogReady}
              title="等待后端开放 Profile Catalog"
            >
              发布 TaskPlanVersion
            </button>
          ) : null}
          <button
            type="button"
            className={styles.savePlan}
            onClick={onCreatePlan}
            disabled={!canOperate}
          >
            {plan ? "新建另一任务方案" : "保存为测试计划"}
          </button>
        </aside>
      </div>
    </>
  );
}
