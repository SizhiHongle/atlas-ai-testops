"use client";

import {
  Activity,
  ArrowRight,
  ArrowUpRight,
  Atom,
  BadgeCheck,
  Bell,
  Bot,
  Box,
  BrainCircuit,
  Camera,
  Check,
  ChevronDown,
  ChevronRight,
  CircleAlert,
  CircleCheck,
  Clock3,
  Command,
  Component,
  Database,
  Eye,
  FileText,
  Filter,
  Fingerprint,
  GitBranch,
  Globe2,
  Grip,
  Hexagon,
  KeyRound,
  Layers3,
  Link2,
  ListChecks,
  Maximize2,
  Menu,
  MessageSquare,
  MousePointer2,
  Network,
  Pause,
  Play,
  Plus,
  Radio,
  RefreshCw,
  Rocket,
  Search,
  Settings2,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  Terminal,
  TestTube2,
  UsersRound,
  WandSparkles,
  X,
  Zap,
  ZoomIn,
  ZoomOut,
  type LucideIcon
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { usePlatformSession } from "../lib/api/auth";
import {
  useFixtureAssetCatalog,
  type DataAtomCatalogItem,
  type DataBlueprintCatalogItem
} from "../lib/api/fixture";
import { useIdentityWallet } from "../lib/api/identity";
import { useInsightBrief } from "../lib/api/insights";
import {
  useFailureClusters,
  useTaskResult,
  useTaskRuns,
  type FailureClusterItem as ApiFailureClusterItem,
  type TaskRun as ApiTaskRun
} from "../lib/api/results";

type ViewId = "space" | "identities" | "atoms" | "compose" | "cases" | "launch" | "live" | "results" | "insights";

const fallbackInsightTerrain = [
  { label: "客户筛选", rate: "97.8%" },
  { label: "权限边界", rate: "92.4%" },
  { label: "来访关系", rate: "99.1%" },
  { label: "身份租约", rate: "96.6%" }
] as const;

function formatInsightRate(
  basisPoints: number | null | undefined,
  fallback: string
): string {
  return basisPoints === null || basisPoints === undefined
    ? fallback
    : `${(basisPoints / 100).toFixed(1)}%`;
}

function formatInsightDelta(
  basisPoints: number | null | undefined,
  fallback: string
): string {
  if (basisPoints === null || basisPoints === undefined) return fallback;
  const sign = basisPoints > 0 ? "+" : "";
  return `较上周期 ${sign}${(basisPoints / 100).toFixed(1)}%`;
}

type AtomSpec = {
  id: string;
  name: string;
  key: string;
  type: "data" | "identity" | "browser" | "assert" | "agent";
  version: string;
  health: number;
  inputs: string[];
  outputs: string[];
  description: string;
};

type WorkflowAsset = {
  id: string;
  name: string;
  category: string;
  version: string;
  refs: number;
  health: number;
  atoms: string[];
  tone: string;
};

type WorkflowPhase = "setup" | "identity" | "execute" | "assert" | "cleanup";

type CanvasPoint = { x: number; y: number };

type WorkflowNode = {
  id: string;
  atomId: string;
  name: string;
  phase: WorkflowPhase;
  kind: string;
  evidence: string;
  output: string;
  position: CanvasPoint;
};

type WorkflowEdge = {
  id: string;
  source: string;
  target: string;
  sourcePort: string;
  targetPort: string;
  label: string;
};

type CaseVersion = {
  id: string;
  version: string;
  caseName: string;
  role: string;
  sourceRevision: number;
  publishedAt: string;
  workflowSnapshot: WorkflowNode[];
  edgeSnapshot: WorkflowEdge[];
};

type TestCase = {
  id: string;
  name: string;
  role: string;
  intent: string;
  draftRevision: number;
  updatedBy: "AI" | "人工";
  workflow: WorkflowNode[];
  edges: WorkflowEdge[];
  lastDebugRevision?: number;
  versions: CaseVersion[];
};

function cloneWorkflow(nodes: WorkflowNode[]): WorkflowNode[] {
  return nodes.map((node) => ({ ...node, position: { ...node.position } }));
}

function cloneEdges(edges: WorkflowEdge[]): WorkflowEdge[] {
  return edges.map((edge) => ({ ...edge }));
}

function orderWorkflowByGraph(nodes: WorkflowNode[], edges: WorkflowEdge[]): WorkflowNode[] {
  const nodeById = new Map(nodes.map((node) => [node.id, node]));
  const orderById = new Map(nodes.map((node, index) => [node.id, index]));
  const indegree = new Map(nodes.map((node) => [node.id, 0]));
  const outgoing = new Map(nodes.map((node) => [node.id, [] as string[]]));

  edges.forEach((edge) => {
    if (!nodeById.has(edge.source) || !nodeById.has(edge.target)) return;
    outgoing.get(edge.source)?.push(edge.target);
    indegree.set(edge.target, (indegree.get(edge.target) ?? 0) + 1);
  });

  const queue = nodes.filter((node) => indegree.get(node.id) === 0);
  const ordered: WorkflowNode[] = [];
  while (queue.length) {
    queue.sort((a, b) => (orderById.get(a.id) ?? 0) - (orderById.get(b.id) ?? 0));
    const node = queue.shift();
    if (!node) break;
    ordered.push(node);
    outgoing.get(node.id)?.forEach((targetId) => {
      const nextDegree = (indegree.get(targetId) ?? 1) - 1;
      indegree.set(targetId, nextDegree);
      if (nextDegree === 0) {
        const target = nodeById.get(targetId);
        if (target) queue.push(target);
      }
    });
  }

  return ordered.length === nodes.length ? ordered : nodes;
}

function wouldCreateCycle(edges: WorkflowEdge[], source: string, target: string): boolean {
  if (source === target) return true;
  const outgoing = new Map<string, string[]>();
  edges.forEach((edge) => outgoing.set(edge.source, [...(outgoing.get(edge.source) ?? []), edge.target]));
  const pending = [target];
  const visited = new Set<string>();
  while (pending.length) {
    const current = pending.pop();
    if (!current || visited.has(current)) continue;
    if (current === source) return true;
    visited.add(current);
    pending.push(...(outgoing.get(current) ?? []));
  }
  return false;
}

const nodePortContracts: Record<string, { inputs: string[]; outputs: string[] }> = {
  account: { inputs: [], outputs: ["identityRef", "authState"] },
  customer: { inputs: [], outputs: ["customerId", "trackingId"] },
  visit: { inputs: ["customerId"], outputs: ["visitId", "visitNo"] },
  open: { inputs: ["authState"], outputs: ["pageRef", "snapshot"] },
  filter: { inputs: ["trackingId", "pageRef"], outputs: ["rows", "evidence"] },
  assert: { inputs: ["rows", "visitId"], outputs: ["result"] },
  cleanup: { inputs: ["result"], outputs: ["cleanupResult"] },
  "asset-data": { inputs: [], outputs: ["customerId", "trackingId", "visitId"] },
  "asset-login": { inputs: [], outputs: ["authState", "pageRef"] },
  "asset-filter": { inputs: ["trackingId", "pageRef"], outputs: ["rows"] },
  "asset-assert": { inputs: ["rows", "visitId"], outputs: ["result"] },
  "asset-cleanup": { inputs: ["result"], outputs: ["cleanupResult"] }
};

function getNodeInputs(node: WorkflowNode): string[] {
  if (node.id.includes("negative")) return ["result"];
  return nodePortContracts[node.id.split("-").slice(0, 2).join("-")]?.inputs ?? nodePortContracts[node.atomId]?.inputs ?? (node.phase === "setup" || node.phase === "identity" ? [] : ["input"]);
}

function getNodeOutputs(node: WorkflowNode): string[] {
  return nodePortContracts[node.id.split("-").slice(0, 2).join("-")]?.outputs ?? nodePortContracts[node.atomId]?.outputs ?? [node.output.split(" · ")[0]];
}

function findCompatiblePort(source: WorkflowNode, target: WorkflowNode, edges: WorkflowEdge[]): { sourcePort: string; targetPort: string } | null {
  const sourcePorts = getNodeOutputs(source);
  const occupiedInputs = new Set(edges.filter((edge) => edge.target === target.id).map((edge) => edge.targetPort));
  const targetPort = getNodeInputs(target).find((input) => !occupiedInputs.has(input) && sourcePorts.includes(input));
  return targetPort ? { sourcePort: targetPort, targetPort } : null;
}

type GraphValidation = { valid: boolean; issues: string[]; matchedPorts: number; totalPorts: number };

function validateWorkflowGraph(nodes: WorkflowNode[], edges: WorkflowEdge[]): GraphValidation {
  const nodeById = new Map(nodes.map((node) => [node.id, node]));
  const issues: string[] = [];
  const validEdges = edges.filter((edge) => nodeById.has(edge.source) && nodeById.has(edge.target));
  if (validEdges.length !== edges.length) issues.push("存在悬空连线");
  validEdges.forEach((edge) => {
    const source = nodeById.get(edge.source);
    const target = nodeById.get(edge.target);
    if (source && !getNodeOutputs(source).includes(edge.sourcePort)) issues.push(`${source.name} 不输出 ${edge.sourcePort}`);
    if (target && !getNodeInputs(target).includes(edge.targetPort)) issues.push(`${target.name} 不接收 ${edge.targetPort}`);
  });

  const indegree = new Map(nodes.map((node) => [node.id, 0]));
  const outgoing = new Map(nodes.map((node) => [node.id, [] as string[]]));
  validEdges.forEach((edge) => {
    indegree.set(edge.target, (indegree.get(edge.target) ?? 0) + 1);
    outgoing.get(edge.source)?.push(edge.target);
  });
  const queue = nodes.filter((node) => indegree.get(node.id) === 0).map((node) => node.id);
  let visited = 0;
  while (queue.length) {
    const current = queue.shift();
    if (!current) continue;
    visited += 1;
    outgoing.get(current)?.forEach((target) => {
      const next = (indegree.get(target) ?? 1) - 1;
      indegree.set(target, next);
      if (next === 0) queue.push(target);
    });
  }
  if (visited !== nodes.length) issues.push("依赖图存在循环");

  let totalPorts = 0;
  let matchedPorts = 0;
  nodes.forEach((node) => {
    const inputs = getNodeInputs(node);
    totalPorts += inputs.length;
    inputs.forEach((input) => {
      const matches = validEdges.filter((edge) => edge.target === node.id && edge.targetPort === input);
      if (matches.length === 1) matchedPorts += 1;
      else if (!matches.length) issues.push(`${node.name} 缺少 ${input}`);
      else issues.push(`${node.name} 的 ${input} 有多个来源`);
    });
    const nodeOutgoing = validEdges.filter((edge) => edge.source === node.id);
    const mayTerminate = node.phase === "assert" && !nodes.some((candidate) => candidate.phase === "cleanup");
    if (!nodeOutgoing.length && node.phase !== "cleanup" && !mayTerminate) issues.push(`${node.name} 未连接后继`);
    if (node.phase === "cleanup" && nodeOutgoing.length) issues.push(`${node.name} 必须是终止节点`);
  });

  return { valid: issues.length === 0, issues: Array.from(new Set(issues)), matchedPorts, totalPorts };
}

const views: Array<{ id: ViewId; label: string }> = [
  { id: "space", label: "测试空间" },
  { id: "identities", label: "身份" },
  { id: "atoms", label: "原子" },
  { id: "compose", label: "资产" },
  { id: "cases", label: "用例" },
  { id: "launch", label: "任务" },
  { id: "live", label: "现场" },
  { id: "results", label: "结果" },
  { id: "insights", label: "洞察" }
];

const atomSpecs: AtomSpec[] = [
  {
    id: "customer",
    name: "创建客户",
    key: "customer.create",
    type: "data",
    version: "2.4",
    health: 99,
    inputs: ["name:string", "ownerId?:UserId"],
    outputs: ["customerId:CustomerId", "trackingId:string"],
    description: "创建隔离客户，并向后续节点输出强类型客户标识与追踪 ID。"
  },
  {
    id: "visit",
    name: "创建来访单",
    key: "visit.create",
    type: "data",
    version: "1.8",
    health: 97,
    inputs: ["customerId:CustomerId", "channel:VisitChannel"],
    outputs: ["visitId:VisitId", "visitNo:string"],
    description: "通过 CustomerId 端口绑定客户，生成可供 UI 与接口共同验证的来访数据。"
  },
  {
    id: "account",
    name: "租用销售身份",
    key: "identity.lease",
    type: "identity",
    version: "3.1",
    health: 96,
    inputs: ["role:RoleKey", "environment:Env"],
    outputs: ["identityRef:Identity", "authState:SecretRef"],
    description: "从身份钱包中租用独占测试账号，并向浏览器输出认证状态。"
  },
  {
    id: "open",
    name: "打开客户空间",
    key: "browser.open",
    type: "browser",
    version: "2.2",
    health: 98,
    inputs: ["authState:SecretRef", "route:AppRoute"],
    outputs: ["pageRef:PageRef", "snapshot:ArtifactRef"],
    description: "创建隔离浏览器上下文并打开业务页面，同时捕获首个页面快照。"
  },
  {
    id: "filter",
    name: "语义筛选",
    key: "agent.semanticAction",
    type: "agent",
    version: "4.0",
    health: 94,
    inputs: ["pageRef:PageRef", "trackingId:string", "intent:string"],
    outputs: ["rows:RowRef[]", "evidence:ArtifactRef[]"],
    description: "Agent 在动作预算内寻找筛选入口并完成目标，不改变既定成功条件。"
  },
  {
    id: "assert",
    name: "关系断言",
    key: "assert.customerVisit",
    type: "assert",
    version: "2.7",
    health: 100,
    inputs: ["rows:RowRef[]", "visitId:VisitId"],
    outputs: ["result:AssertionResult"],
    description: "通过 DOM 与 API 双通道校验客户、追踪 ID 和来访单绑定关系。"
  }
];

const identities = [
  { id: "sales", role: "销售", available: 11, total: 14, leased: 3, health: 98, environment: "PRE / TEST", color: "mint", account: "sales-11", powers: ["读取客户", "创建来访", "查看本人数据"] },
  { id: "manager", role: "主管", available: 4, total: 5, leased: 1, health: 98, environment: "PRE / TEST", color: "violet", account: "manager-02", powers: ["团队视图", "角色筛选", "分配归属人"] },
  { id: "service", role: "客服", available: 6, total: 8, leased: 2, health: 98, environment: "PRE / TEST", color: "coral", account: "service-04", powers: ["客户只读", "工单处理", "禁止改归属"] },
  { id: "admin", role: "管理员", available: 2, total: 2, leased: 0, health: 98, environment: "PRE / TEST", color: "ink", account: "admin-qa", powers: ["全域访问", "策略管理", "账号维护"] }
];

const customerWorkflow: WorkflowNode[] = [
  { id: "n-account", atomId: "account", name: "租用销售身份", phase: "identity", kind: "Identity", evidence: "sales-11", output: "identityRef", position: { x: 68, y: 300 } },
  { id: "n-customer", atomId: "customer", name: "创建客户", phase: "setup", kind: "API", evidence: "TRK-018", output: "customerId · trackingId", position: { x: 68, y: 72 } },
  { id: "n-visit", atomId: "visit", name: "创建来访单", phase: "setup", kind: "API", evidence: "VIS-2048", output: "visitId", position: { x: 292, y: 72 } },
  { id: "n-open", atomId: "open", name: "打开客户空间", phase: "execute", kind: "Browser", evidence: "200 OK", output: "pageRef", position: { x: 292, y: 300 } },
  { id: "n-filter", atomId: "filter", name: "按追踪 ID 筛选", phase: "execute", kind: "Agent", evidence: "8 actions", output: "matchedRows", position: { x: 548, y: 214 } },
  { id: "n-assert", atomId: "assert", name: "验证客户关系", phase: "assert", kind: "Assert", evidence: "3 / 3", output: "result", position: { x: 804, y: 150 } }
];

const customerEdges: WorkflowEdge[] = [
  { id: "e-customer-visit", source: "n-customer", target: "n-visit", sourcePort: "customerId", targetPort: "customerId", label: "customerId" },
  { id: "e-account-open", source: "n-account", target: "n-open", sourcePort: "authState", targetPort: "authState", label: "authState" },
  { id: "e-customer-filter", source: "n-customer", target: "n-filter", sourcePort: "trackingId", targetPort: "trackingId", label: "trackingId" },
  { id: "e-open-filter", source: "n-open", target: "n-filter", sourcePort: "pageRef", targetPort: "pageRef", label: "pageRef" },
  { id: "e-visit-assert", source: "n-visit", target: "n-assert", sourcePort: "visitId", targetPort: "visitId", label: "visitId" },
  { id: "e-filter-assert", source: "n-filter", target: "n-assert", sourcePort: "rows", targetPort: "rows", label: "rows" }
];

const managerWorkflow: WorkflowNode[] = customerWorkflow.map((node) => node.atomId === "account" ? { ...node, id: "m-account", name: "租用主管身份", evidence: "manager-02" } : { ...node, id: `m-${node.id}` });
const serviceWorkflow: WorkflowNode[] = customerWorkflow.map((node) => node.atomId === "account" ? { ...node, id: "s-account", name: "租用客服身份", evidence: "service-04" } : { ...node, id: `s-${node.id}` });
const managerNodeId = (id: string) => id === "n-account" ? "m-account" : `m-${id}`;
const serviceNodeId = (id: string) => id === "n-account" ? "s-account" : `s-${id}`;
const managerEdges: WorkflowEdge[] = customerEdges.map((edge) => ({ ...edge, id: `m-${edge.id}`, source: managerNodeId(edge.source), target: managerNodeId(edge.target) }));
const serviceEdges: WorkflowEdge[] = customerEdges.map((edge) => ({ ...edge, id: `s-${edge.id}`, source: serviceNodeId(edge.source), target: serviceNodeId(edge.target) }));

const initialCases: TestCase[] = [
  { id: "TC-1042", name: "销售筛选客户", role: "销售", intent: "创建客户并绑定来访单，由销售身份通过追踪 ID 精确筛选，并验证客户与来访关系。", draftRevision: 7, updatedBy: "AI", workflow: customerWorkflow, edges: customerEdges, lastDebugRevision: 7, versions: [{ id: "TC-1042@v1.3", version: "v1.3", caseName: "销售筛选客户", role: "销售", sourceRevision: 6, publishedAt: "07-10 18:42", workflowSnapshot: cloneWorkflow(customerWorkflow), edgeSnapshot: cloneEdges(customerEdges) }] },
  { id: "TC-1043", name: "主管团队范围", role: "主管", intent: "验证主管仅能筛选所属团队客户，并能切换归属人视角。", draftRevision: 4, updatedBy: "人工", workflow: managerWorkflow, edges: managerEdges, lastDebugRevision: 4, versions: [{ id: "TC-1043@v1.2", version: "v1.2", caseName: "主管团队范围", role: "主管", sourceRevision: 4, publishedAt: "07-09 16:15", workflowSnapshot: cloneWorkflow(managerWorkflow), edgeSnapshot: cloneEdges(managerEdges) }] },
  { id: "TC-1091", name: "客服权限边界", role: "客服", intent: "验证客服可以查看客户与来访，但无法修改客户归属人。", draftRevision: 3, updatedBy: "AI", workflow: serviceWorkflow, edges: serviceEdges, versions: [{ id: "TC-1091@v0.9", version: "v0.9", caseName: "客服权限边界", role: "客服", sourceRevision: 2, publishedAt: "07-08 14:06", workflowSnapshot: cloneWorkflow(serviceWorkflow), edgeSnapshot: cloneEdges(serviceEdges) }] }
];

const workflowAssets: WorkflowAsset[] = [
  { id: "asset-data", name: "客户 + 来访数据链", category: "数据初始化", version: "v2.6", refs: 18, health: 99, atoms: ["customer", "visit"], tone: "mint" },
  { id: "asset-login", name: "角色身份登录", category: "身份子流程", version: "v3.1", refs: 26, health: 98, atoms: ["account", "open"], tone: "sand" },
  { id: "asset-filter", name: "语义筛选动作", category: "Agent 能力", version: "v4.0", refs: 12, health: 94, atoms: ["filter"], tone: "violet" },
  { id: "asset-assert", name: "客户关系双通道断言", category: "断言模板", version: "v2.7", refs: 21, health: 100, atoms: ["assert"], tone: "coral" },
  { id: "asset-cleanup", name: "测试数据清理补偿", category: "清理策略", version: "v1.4", refs: 9, health: 97, atoms: [], tone: "blue" }
];

function fixtureAssetHealth(status: string | null | undefined): number {
  if (status === "PUBLISHED") return 100;
  if (status === "VALIDATED") return 98;
  if (status === "DEPRECATED") return 82;
  return 90;
}

function projectDataAtoms(items: DataAtomCatalogItem[]): AtomSpec[] {
  if (!items.length) return atomSpecs;
  let dataIndex = 0;
  return atomSpecs.map((slot): AtomSpec => {
    if (slot.type !== "data") return slot;
    const item = items[dataIndex];
    dataIndex += 1;
    if (!item) return slot;
    return {
      ...slot,
      name: item.name,
      key: item.atomKey,
      version: item.latestVersion ?? "0.0-draft",
      health: fixtureAssetHealth(item.latestVersionStatus),
      inputs: item.inputPorts,
      outputs: item.outputPorts,
      description: item.description
    };
  });
}

function projectDataBlueprints(
  items: DataBlueprintCatalogItem[],
  projectedAtoms: AtomSpec[]
): WorkflowAsset[] {
  const blueprint = items.find((item) => item.latestVersionId && item.nodeCount > 0);
  if (!blueprint) return workflowAssets;
  const dataAtomIds = projectedAtoms
    .filter((item) => item.type === "data")
    .slice(0, blueprint.nodeCount)
    .map((item) => item.id);
  return workflowAssets.map((asset) => asset.id === "asset-data" ? {
    ...asset,
    name: blueprint.name,
    version: `v${blueprint.latestVersion ?? "0.0-draft"}`,
    refs: 0,
    health: fixtureAssetHealth(blueprint.latestVersionStatus),
    atoms: dataAtomIds
  } : asset);
}

type TaskStatus = "running" | "attention" | "queued" | "passed";

type TaskRun = {
  id: string;
  backendId?: string;
  name: string;
  trigger: string;
  status: TaskStatus;
  progress: number;
  executions: number;
  passed: number;
  failed: number;
  eta: string;
  matrix: string;
  roles: string[];
  browsers: string[];
  caseVersionIds?: string[];
};

const taskRuns: TaskRun[] = [
  { id: "TASK-2048", name: "R26.07 每日核心回归", trigger: "定时 · 每日 21:30", status: "running", progress: 72, executions: 120, passed: 78, failed: 4, eta: "08:42", matrix: "40 用例 × 3 角色 × 1 浏览器", roles: ["销售", "主管", "客服"], browsers: ["Chromium"], caseVersionIds: ["TC-1042@v1.3", "TC-1043@v1.2", "TC-1091@v0.9"] },
  { id: "TASK-2047", name: "客户权限发布门禁", trigger: "发布事件 · build 816", status: "attention", progress: 100, executions: 84, passed: 79, failed: 5, eta: "已完成", matrix: "28 用例 × 3 角色 × 1 浏览器", roles: ["销售", "主管", "客服"], browsers: ["Chromium"], caseVersionIds: ["TC-1042@v1.3", "TC-1043@v1.2", "TC-1091@v0.9"] },
  { id: "TASK-2046", name: "预发全量夜间回归", trigger: "定时 · 今日 23:00", status: "queued", progress: 0, executions: 240, passed: 0, failed: 0, eta: "23:00", matrix: "40 用例 × 3 角色 × 2 浏览器", roles: ["销售", "主管", "客服"], browsers: ["Chromium", "WebKit"], caseVersionIds: ["TC-1042@v1.3", "TC-1043@v1.2", "TC-1091@v0.9"] },
  { id: "TASK-2045", name: "来访关系冒烟验证", trigger: "CI · commit a82f", status: "passed", progress: 100, executions: 36, passed: 36, failed: 0, eta: "06:18", matrix: "12 用例 × 3 角色 × 1 浏览器", roles: ["销售", "主管", "客服"], browsers: ["Chromium"], caseVersionIds: ["TC-1042@v1.3", "TC-1043@v1.2", "TC-1091@v0.9"] }
];

const taskFailureClusters = [
  { id: "api", name: "客户查询接口 502", kind: "产品问题", count: 3, impact: "6 个 Execution", confidence: 96, tone: "coral" },
  { id: "selector", name: "筛选入口语义漂移", kind: "测试方法", count: 1, impact: "1 个 Execution", confidence: 88, tone: "violet" },
  { id: "session", name: "客服登录态过期", kind: "环境问题", count: 1, impact: "1 个 Execution", confidence: 93, tone: "sand" }
];

function projectApiTaskRun(run: ApiTaskRun): TaskRun {
  const closed = run.lifecycle === "CLOSED";
  const passed = closed && run.quality === "PASSED";
  const executions = run.materializedUnitCount ?? run.materializedFirstAttemptCount ?? 0;
  const triggerLabels: Record<ApiTaskRun["triggerSource"], string> = {
    MANUAL: "手动",
    SCHEDULE: "定时",
    CI: "CI",
    WEBHOOK: "Webhook",
    API: "API"
  };
  return {
    id: `TASK-${run.id.slice(0, 8).toUpperCase()}`,
    backendId: run.id,
    name: `正式任务 · ${run.taskPlanVersionId.slice(0, 8)}`,
    trigger: `${triggerLabels[run.triggerSource]} · immutable`,
    status: passed ? "passed" : closed ? "attention" : run.lifecycle === "QUEUED" ? "queued" : "running",
    progress: closed ? 100 : run.lifecycle === "QUEUED" ? 0 : 50,
    executions,
    passed: passed ? executions : 0,
    failed: closed && !passed ? 1 : 0,
    eta: closed ? "已完成" : run.lifecycle === "QUEUED" ? "等待调度" : "执行中",
    matrix: `${executions} ExecutionUnit · Manifest locked`,
    roles: ["CaseVersion 绑定身份"],
    browsers: ["BrowserProfile locked"]
  };
}

function failureDomainLabel(domain: string | undefined): string {
  if (domain === "PRODUCT") return "产品问题";
  if (domain === "TEST_SPEC" || domain === "TEST_DATA" || domain === "AGENT_AUTOMATION") return "测试方法";
  if (domain === "POLICY_SECURITY") return "策略安全";
  if (domain === "EVIDENCE") return "证据问题";
  if (domain === "CLEANUP") return "清理问题";
  return "环境问题";
}

function projectFailureClusters(items: ApiFailureClusterItem[]) {
  const tones = ["coral", "violet", "sand"] as const;
  return items.map((item, index) => {
    const classification = item.classification;
    const confidence = classification
      ? Math.round(classification.confidence.numerator / classification.confidence.denominator * 100)
      : 0;
    return {
      id: item.cluster.id,
      name: classification?.hypothesis ?? item.cluster.signal.signalCode,
      kind: failureDomainLabel(classification?.failureDomain),
      count: item.cluster.affectedCount,
      impact: `${item.cluster.affectedCount} 个 Execution`,
      confidence,
      tone: tones[index % tones.length]
    };
  });
}

type ExecutionState = "passed" | "running" | "queued" | "product" | "infra" | "flaky";

function executionStateAt(index: number, completed: number, total: number, active: boolean, injectFailures: boolean): ExecutionState {
  if (active && index === completed && completed < total) return "running";
  if (index >= completed || completed === 0) return "queued";
  if (!injectFailures) return "passed";
  const at = (ratio: number) => Math.min(total - 1, Math.max(0, Math.floor(total * ratio)));
  if ([.08, .29, .51, .69].map(at).includes(index)) return "product";
  if ([.18, .62].map(at).includes(index)) return "infra";
  if ([.25, .68].map(at).includes(index)) return "flaky";
  return "passed";
}

function MiniButton({ icon: Icon, label, onClick, active = false }: { icon: LucideIcon; label: string; onClick?: () => void; active?: boolean }) {
  return <button type="button" className={`mini-button ${active ? "is-active" : ""}`} onClick={onClick}><Icon size={15} /><span>{label}</span></button>;
}

function SceneIntro({ eyebrow, title, description, action }: { eyebrow: string; title: string; description: string; action?: React.ReactNode }) {
  return (
    <div className="scene-intro">
      <div>
        <div className="scene-eyebrow"><Sparkles size={13} />{eyebrow}</div>
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      {action && <div className="scene-actions">{action}</div>}
    </div>
  );
}

function StatusPill({ children, tone = "neutral" }: { children: React.ReactNode; tone?: "neutral" | "good" | "warn" | "violet" | "dark" }) {
  return <span className={`status-pill status-${tone}`}>{children}</span>;
}

function getCaseState(item: TestCase): { label: string; tone: "neutral" | "good" | "warn" | "violet" | "dark" } {
  const latest = item.versions[0];
  if (latest?.sourceRevision === item.draftRevision) return { label: `${latest.version} 已发布`, tone: "good" };
  if (item.lastDebugRevision === item.draftRevision) return { label: "调试通过 · 待发布", tone: "violet" };
  if (latest) return { label: `${latest.version} · 有草稿变更`, tone: "warn" };
  return { label: "草稿待调试", tone: "neutral" };
}

function TestOrbit() {
  return (
    <div className="test-orbit" aria-label="客户筛选测试依赖路径">
      <div className="orbit-halo halo-one" />
      <div className="orbit-halo halo-two" />
      <svg viewBox="0 0 620 370" aria-hidden="true">
        <defs>
          <linearGradient id="orbitLine" x1="0" x2="1">
            <stop offset="0" stopColor="#15171a" />
            <stop offset=".48" stopColor="#5468f2" />
            <stop offset="1" stopColor="#15171a" />
          </linearGradient>
        </defs>
        <path d="M82 232 C150 90 280 74 345 152 C414 234 492 252 555 116" className="orbit-path-base" />
        <path d="M82 232 C150 90 280 74 345 152 C414 234 492 252 555 116" className="orbit-path-flow" />
      </svg>
      <div className="orbit-node node-identity"><Fingerprint size={17} /><span>身份</span><b>sales-11</b></div>
      <div className="orbit-node node-customer"><Database size={17} /><span>客户</span><b>TRK-018</b></div>
      <div className="orbit-node node-visit"><Link2 size={17} /><span>来访</span><b>VIS-2048</b></div>
      <div className="orbit-node node-agent"><Bot size={17} /><span>Agent</span><b>筛选</b></div>
      <div className="orbit-node node-assert"><ShieldCheck size={17} /><span>断言</span><b>3 / 3</b></div>
      <div className="orbit-center"><span>ACTIVE SPACE</span><strong>94.6%</strong><small>本迭代稳定度</small></div>
    </div>
  );
}

function WorkflowNodeGlyph({ node, size = 18 }: { node: WorkflowNode; size?: number }) {
  if (node.atomId === "account" || node.atomId === "asset-login") return <Fingerprint size={size} />;
  if (node.atomId === "customer" || node.atomId === "asset-data") return <Database size={size} />;
  if (node.atomId === "visit") return <Link2 size={size} />;
  if (node.atomId === "open") return <Eye size={size} />;
  if (node.atomId === "filter" || node.atomId === "asset-filter") return <Bot size={size} />;
  if (node.phase === "cleanup") return <RefreshCw size={size} />;
  return <ShieldCheck size={size} />;
}

function WorkflowCanvas({
  nodes,
  edges,
  canvasKey,
  selectedNodeId,
  draftRevision,
  editable,
  onSelectNode,
  onNodePositionChange,
  onConnect,
  onDeleteEdge,
  onAutoLayout,
  onOpenAssets
}: {
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
  canvasKey: string;
  selectedNodeId: string;
  draftRevision: number;
  editable: boolean;
  onSelectNode: (nodeId: string) => void;
  onNodePositionChange: (nodeId: string, position: CanvasPoint, commit: boolean) => void;
  onConnect: (source: string, target: string) => void;
  onDeleteEdge: (edgeId: string) => void;
  onAutoLayout: () => void;
  onOpenAssets: () => void;
}) {
  const viewportRef = useRef<HTMLDivElement>(null);
  const [zoom, setZoom] = useState(.72);
  const [pan, setPan] = useState({ x: 8, y: 8 });
  const [panDrag, setPanDrag] = useState<{ pointerId: number; startX: number; startY: number; origin: CanvasPoint } | null>(null);
  const [drag, setDrag] = useState<{ nodeId: string; pointerId: number; startX: number; startY: number; origin: CanvasPoint; last: CanvasPoint; moved: boolean } | null>(null);
  const [connectingFrom, setConnectingFrom] = useState<string | null>(null);
  const selected = nodes.find((node) => node.id === selectedNodeId) ?? nodes[0];
  const nodeWidth = 160;
  const nodeHeight = 108;
  const stageWidth = 1180;
  const stageHeight = 540;
  const displayNodes = drag ? nodes.map((node) => node.id === drag.nodeId ? { ...node, position: drag.last } : node) : nodes;
  const drawableEdges = edges.flatMap((edge) => {
    const from = displayNodes.find((node) => node.id === edge.source);
    const to = displayNodes.find((node) => node.id === edge.target);
    return from && to ? [{ ...edge, from, to }] : [];
  });
  const selectedEdges = selected ? edges.filter((edge) => edge.source === selected.id || edge.target === selected.id) : [];

  useEffect(() => {
    setZoom(.72);
    setPan({ x: 8, y: 8 });
    setConnectingFrom(null);
  }, [canvasKey]);

  useEffect(() => {
    if (!editable) setConnectingFrom(null);
  }, [editable]);

  function beginDrag(event: React.PointerEvent<HTMLDivElement>, node: WorkflowNode) {
    event.stopPropagation();
    onSelectNode(node.id);
    if (!editable) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    setDrag({ nodeId: node.id, pointerId: event.pointerId, startX: event.clientX, startY: event.clientY, origin: node.position, last: node.position, moved: false });
  }

  function beginPan(event: React.PointerEvent<HTMLDivElement>) {
    if ((event.target as HTMLElement).closest(".canvas-node, .canvas-edge-hit, .canvas-toolrail, .canvas-minimap, .canvas-node-inspector, .canvas-connect-hint")) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    setPanDrag({ pointerId: event.pointerId, startX: event.clientX, startY: event.clientY, origin: pan });
  }

  function movePan(event: React.PointerEvent<HTMLDivElement>) {
    if (!panDrag || event.pointerId !== panDrag.pointerId) return;
    setPan({ x: panDrag.origin.x + event.clientX - panDrag.startX, y: panDrag.origin.y + event.clientY - panDrag.startY });
  }

  function endPan(event: React.PointerEvent<HTMLDivElement>) {
    if (!panDrag || event.pointerId !== panDrag.pointerId) return;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    setPanDrag(null);
  }

  function moveDrag(event: React.PointerEvent<HTMLDivElement>) {
    if (!drag || event.pointerId !== drag.pointerId) return;
    const next = {
      x: Math.max(18, Math.min(stageWidth - nodeWidth - 18, drag.origin.x + (event.clientX - drag.startX) / zoom)),
      y: Math.max(18, Math.min(stageHeight - nodeHeight - 18, drag.origin.y + (event.clientY - drag.startY) / zoom))
    };
    const moved = drag.moved || Math.abs(next.x - drag.origin.x) > 1 || Math.abs(next.y - drag.origin.y) > 1;
    setDrag({ ...drag, last: next, moved });
  }

  function endDrag(event: React.PointerEvent<HTMLDivElement>) {
    if (!drag || event.pointerId !== drag.pointerId) return;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    if (drag.moved) onNodePositionChange(drag.nodeId, drag.last, true);
    setDrag(null);
  }

  function cancelDrag(event: React.PointerEvent<HTMLDivElement>) {
    if (!drag || event.pointerId !== drag.pointerId) return;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    setDrag(null);
  }

  function moveNodeByKeyboard(event: React.KeyboardEvent<HTMLDivElement>, node: WorkflowNode) {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onSelectNode(node.id);
      return;
    }
    if (!editable) return;
    const delta = event.shiftKey ? 32 : 12;
    const movement = event.key === "ArrowLeft" ? { x: -delta, y: 0 } : event.key === "ArrowRight" ? { x: delta, y: 0 } : event.key === "ArrowUp" ? { x: 0, y: -delta } : event.key === "ArrowDown" ? { x: 0, y: delta } : null;
    if (!movement) return;
    event.preventDefault();
    onSelectNode(node.id);
    onNodePositionChange(node.id, { x: Math.max(18, Math.min(stageWidth - nodeWidth - 18, node.position.x + movement.x)), y: Math.max(18, Math.min(stageHeight - nodeHeight - 18, node.position.y + movement.y)) }, true);
  }

  function fitView() {
    const bounds = viewportRef.current?.getBoundingClientRect();
    if (!bounds) return;
    const nextZoom = Math.max(.42, Math.min(1.04, Math.min((bounds.width - 28) / stageWidth, (bounds.height - 28) / stageHeight)));
    setZoom(Number(nextZoom.toFixed(2)));
    setPan({ x: Math.max(14, (bounds.width - stageWidth * nextZoom) / 2), y: Math.max(14, (bounds.height - stageHeight * nextZoom) / 2) });
  }

  function startConnection(event: React.SyntheticEvent, nodeId: string) {
    event.stopPropagation();
    if (!editable) return;
    setConnectingFrom(nodeId);
    onSelectNode(nodeId);
  }

  function finishConnection(event: React.SyntheticEvent, nodeId: string) {
    event.stopPropagation();
    if (!editable || !connectingFrom || connectingFrom === nodeId) return;
    onConnect(connectingFrom, nodeId);
    setConnectingFrom(null);
  }

  function handlePortKey(event: React.KeyboardEvent<HTMLSpanElement>, action: () => void) {
    event.stopPropagation();
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    action();
  }

  return (
    <div className={`workflow-graph-canvas ${editable ? "is-editable" : "is-ai-mode"}`}>
      <div className="canvas-topbar"><div><GitBranch size={15} /><span>WORKFLOW GRAPH</span><strong>{nodes.length} NODES · {edges.length} EDGES</strong></div><StatusPill tone={editable ? "good" : "violet"}>{editable ? "端口可连接 · 连线可删除" : "AI Patch 共编模式"}</StatusPill><small>Draft r{draftRevision}</small></div>
      <div ref={viewportRef} className={`canvas-viewport ${panDrag ? "is-panning" : ""}`} onPointerDown={beginPan} onPointerMove={movePan} onPointerUp={endPan} onPointerCancel={endPan}>
        <div className="canvas-grid" />
        <div className="canvas-stage" style={{ width: stageWidth, height: stageHeight, transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})` }}>
          <svg className="canvas-edges" viewBox={`0 0 ${stageWidth} ${stageHeight}`} aria-hidden="true">
            <defs><marker id="canvasArrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" /></marker></defs>
            {drawableEdges.map((edge) => {
              const startX = edge.from.position.x + nodeWidth;
              const startY = edge.from.position.y + nodeHeight / 2;
              const endX = edge.to.position.x;
              const endY = edge.to.position.y + nodeHeight / 2;
              const curve = Math.max(56, Math.abs(endX - startX) * .42);
              const path = `M ${startX} ${startY} C ${startX + curve} ${startY}, ${endX - curve} ${endY}, ${endX} ${endY}`;
              return <g key={edge.id}><path className="canvas-edge-hit" d={path} onClick={(event) => { event.stopPropagation(); if (editable) onDeleteEdge(edge.id); }}><title>{editable ? `删除连线 ${edge.label}` : edge.label}</title></path><path className="canvas-edge-shadow" d={path} /><path className="canvas-edge-flow" d={path} markerEnd="url(#canvasArrow)" /><text x={(startX + endX) / 2} y={(startY + endY) / 2 - 8}>{edge.label}</text></g>;
            })}
          </svg>
          {displayNodes.map((node, index) => <div key={node.id} role="button" tabIndex={0} aria-label={`${node.name}，${editable ? "可拖拽，方向键可移动" : "AI 编排节点"}`} className={`canvas-node canvas-node-${node.phase} ${selectedNodeId === node.id ? "selected" : ""} ${drag?.nodeId === node.id ? "dragging" : ""}`} style={{ left: node.position.x, top: node.position.y }} onPointerDown={(event) => beginDrag(event, node)} onPointerMove={moveDrag} onPointerUp={endDrag} onPointerCancel={cancelDrag} onKeyDown={(event) => moveNodeByKeyboard(event, node)}><span className="canvas-node-index">{String(index + 1).padStart(2, "0")}</span><i className="canvas-node-icon"><WorkflowNodeGlyph node={node} /></i><div><small>{node.phase} · {node.kind}</small><strong>{node.name}</strong><code>{node.output}</code></div><span className="canvas-port port-in" role="button" tabIndex={editable ? 0 : -1} aria-label={`连接到 ${node.name}`} onPointerDown={(event) => event.stopPropagation()} onClick={(event) => finishConnection(event, node.id)} onKeyDown={(event) => handlePortKey(event, () => finishConnection(event, node.id))} /><span className={`canvas-port port-out ${connectingFrom === node.id ? "connecting" : ""}`} role="button" tabIndex={editable ? 0 : -1} aria-label={`从 ${node.name} 创建连线`} onPointerDown={(event) => event.stopPropagation()} onClick={(event) => startConnection(event, node.id)} onKeyDown={(event) => handlePortKey(event, () => startConnection(event, node.id))} /><Grip className="canvas-drag-handle" size={13} /></div>)}
        </div>
        {connectingFrom && <div className="canvas-connect-hint"><Link2 size={13} /><span>选择目标节点 · 自动匹配类型端口</span><button aria-label="取消连线" onClick={() => setConnectingFrom(null)}><X size={12} /></button></div>}
        <div className="canvas-toolrail"><button aria-label="缩小画布" onClick={() => setZoom((value) => Math.max(.42, Number((value - .08).toFixed(2))))}><ZoomOut size={15} /></button><span>{Math.round(zoom * 100)}%</span><button aria-label="放大画布" onClick={() => setZoom((value) => Math.min(1.08, Number((value + .08).toFixed(2))))}><ZoomIn size={15} /></button><i /><button aria-label="适配视图" onClick={fitView}><Maximize2 size={15} /></button><button aria-label="自动布局" disabled={!editable} onClick={onAutoLayout}><GitBranch size={15} /></button><button className="canvas-assets-button" onClick={onOpenAssets}><Component size={14} />资产</button></div>
        <div className="canvas-minimap"><span>MINIMAP</span><div>{displayNodes.map((node) => <i key={node.id} className={`mini-${node.phase}`} style={{ left: `${node.position.x / stageWidth * 100}%`, top: `${node.position.y / stageHeight * 100}%` }} />)}</div></div>
        {selected && <aside className="canvas-node-inspector"><div><span>SELECTED NODE</span><StatusPill tone="neutral">{selected.phase}</StatusPill></div><strong>{selected.name}</strong><code>{selected.output}</code><small>{selected.evidence}</small><div className="canvas-node-links"><span>PORT LINKS · {selectedEdges.length}</span>{selectedEdges.slice(0, 3).map((edge) => <button key={edge.id} disabled={!editable} onClick={() => onDeleteEdge(edge.id)}><i>{edge.source === selected.id ? "OUT" : "IN"}</i><b>{edge.label}</b>{editable && <X size={10} />}</button>)}</div></aside>}
      </div>
    </div>
  );
}

export default function Home() {
  const { data: platformSession } = usePlatformSession();
  const { data: identityWallet } = useIdentityWallet(platformSession?.project.id ?? null);
  const { data: fixtureAssetCatalog } = useFixtureAssetCatalog(
    platformSession?.project.id ?? null
  );
  const { data: backendTaskRunPage } = useTaskRuns(
    platformSession?.project.id ?? null
  );
  const [view, setView] = useState<ViewId>("space");
  const { data: insightBrief } = useInsightBrief(
    platformSession?.project.id ?? null,
    view === "insights"
  );
  const [mobileNav, setMobileNav] = useState(false);
  const [selectedAtom, setSelectedAtom] = useState("customer");
  const [selectedIdentity, setSelectedIdentity] = useState("sales");
  const [selectedNode, setSelectedNode] = useState("n-filter");
  const [selectedAssetId, setSelectedAssetId] = useState("asset-data");
  const [testCases, setTestCases] = useState<TestCase[]>(initialCases);
  const [selectedCaseId, setSelectedCaseId] = useState("TC-1042");
  const [workflowMode, setWorkflowMode] = useState<"ai" | "manual">("ai");
  const [taskVersionIds, setTaskVersionIds] = useState(["TC-1042@v1.3", "TC-1043@v1.2", "TC-1091@v0.9"]);
  const [debugContext, setDebugContext] = useState<{ caseId: string; caseName: string; draftRevision: number; steps: WorkflowNode[]; edges: WorkflowEdge[] } | null>(null);
  const [running, setRunning] = useState(true);
  const [liveMode, setLiveMode] = useState<"single" | "task">("task");
  const [runStep, setRunStep] = useState(4);
  const [taskPanel, setTaskPanel] = useState<"center" | "create">("center");
  const [taskViewMode, setTaskViewMode] = useState<"orbit" | "list">("orbit");
  const [selectedTaskId, setSelectedTaskId] = useState("TASK-2048");
  const [selectedTaskStatus, setSelectedTaskStatus] = useState<TaskStatus>("running");
  const [selectedTaskTotal, setSelectedTaskTotal] = useState(120);
  const [selectedTaskMatrix, setSelectedTaskMatrix] = useState("40 用例 × 3 角色 × 1 浏览器");
  const [selectedTaskRoles, setSelectedTaskRoles] = useState(["销售", "主管", "客服"]);
  const [selectedTaskBrowsers, setSelectedTaskBrowsers] = useState(["Chromium"]);
  const [taskProgress, setTaskProgress] = useState(72);
  const [selectedExecution, setSelectedExecution] = useState(17);
  const [matrixDimension, setMatrixDimension] = useState<"role" | "browser">("role");
  const [selectedCluster, setSelectedCluster] = useState("api");
  const [taskBrowsers, setTaskBrowsers] = useState(["Chromium"]);
  const [taskTrigger, setTaskTrigger] = useState("立即执行");
  const [scheduledTask, setScheduledTask] = useState<TaskRun | null>(null);
  const [toast, setToast] = useState("");
  const backendTaskRuns = useMemo(
    () => (backendTaskRunPage?.items ?? []).map(projectApiTaskRun),
    [backendTaskRunPage?.items]
  );
  const baseTaskRuns = backendTaskRuns.length
    ? [...backendTaskRuns, ...taskRuns]
    : taskRuns;
  const visibleTaskRuns = scheduledTask
    ? [scheduledTask, ...baseTaskRuns]
    : baseTaskRuns;
  const selectedTaskRecord = visibleTaskRuns.find(
    (task) => task.id === selectedTaskId
  );
  const activeTask = scheduledTask?.status === "running"
    ? scheduledTask
    : selectedTaskRecord?.status === "running"
      ? selectedTaskRecord
      : baseTaskRuns.find((task) => task.status === "running") ?? baseTaskRuns[0];
  const { data: backendTaskResult } = useTaskResult(
    view === "results" ? selectedTaskRecord?.backendId ?? null : null
  );
  const { data: backendFailureClusterPage } = useFailureClusters(
    view === "results"
      ? backendTaskResult?.resultSnapshot.id ?? null
      : null
  );
  const projectedFailureClusters = useMemo(
    () => projectFailureClusters(backendFailureClusterPage?.items ?? []),
    [backendFailureClusterPage?.items]
  );
  const currentProjectName = platformSession?.project.name ?? "客户运营";
  const compactProjectName = currentProjectName.length > 8
    ? `${currentProjectName.slice(0, 8)}…`
    : currentProjectName;
  const currentProjectMark = platformSession?.project.projectKey.slice(0, 3) ?? "CRM";
  const currentUserMark = platformSession?.user.displayName.trim().slice(0, 2) ?? "CH";
  const identityCards = identities.map((fallback, index) => {
    const entry = identityWallet?.entries[index];
    if (!entry) return fallback;
    const unresolved = entry.capacity.quarantinedAccounts + entry.capacity.unverifiedAccounts;
    const health = entry.capacity.totalSlots
      ? Math.max(0, Math.round((entry.capacity.totalSlots - unresolved) / entry.capacity.totalSlots * 100))
      : 0;
    return {
      ...fallback,
      role: entry.role.name,
      available: entry.capacity.availableSlots,
      total: entry.capacity.totalSlots,
      leased: entry.capacity.leasedSlots,
      health,
      environment: `${entry.environment.environmentKey.toUpperCase()} / ${entry.environment.kind}`,
      account: entry.account?.accountKey ?? entry.pool.poolKey,
      powers: entry.role.capabilities.length ? entry.role.capabilities.slice(0, 3) : fallback.powers
    };
  });
  const totalIdentityAvailable = identityCards.reduce((total, item) => total + item.available, 0);
  const totalIdentityLeased = identityCards.reduce((total, item) => total + item.leased, 0);

  const visibleAtomSpecs = useMemo(
    () => projectDataAtoms(fixtureAssetCatalog?.atoms ?? []),
    [fixtureAssetCatalog?.atoms]
  );
  const visibleWorkflowAssets = useMemo(
    () => projectDataBlueprints(
      fixtureAssetCatalog?.blueprints ?? [],
      visibleAtomSpecs
    ),
    [fixtureAssetCatalog?.blueprints, visibleAtomSpecs]
  );
  const atom = visibleAtomSpecs.find((item) => item.id === selectedAtom) ?? visibleAtomSpecs[0];
  const identity = identityCards.find((item) => item.id === selectedIdentity) ?? identityCards[0];
  const selectedAsset = visibleWorkflowAssets.find((item) => item.id === selectedAssetId) ?? visibleWorkflowAssets[0];
  const selectedCase = testCases.find((item) => item.id === selectedCaseId) ?? testCases[0];
  const graphValidation = validateWorkflowGraph(selectedCase.workflow, selectedCase.edges);
  const publishedVersions = testCases.flatMap((item) => item.versions.map((version) => ({ ...version, caseId: item.id, caseName: version.caseName, role: version.role })));
  const selectedTaskVersions = taskVersionIds.map((id) => publishedVersions.find((version) => version.id === id)).filter((version): version is NonNullable<typeof version> => Boolean(version));
  const selectedCaseCount = selectedTaskVersions.length;
  const caseBoundRoles = Array.from(new Set(selectedTaskVersions.map((version) => version.role)));
  const plannedExecutions = selectedCaseCount * taskBrowsers.length;
  const activeDebugSteps = debugContext?.steps ?? orderWorkflowByGraph(selectedCase.workflow, selectedCase.edges);
  const selectedLatestVersion = selectedCase.versions[0];
  const currentDraftDebugged = selectedCase.lastDebugRevision === selectedCase.draftRevision;
  const currentDraftPublished = selectedLatestVersion?.sourceRevision === selectedCase.draftRevision;
  const debugFilterIndex = Math.max(0, activeDebugSteps.findIndex((step) => step.atomId === "filter"));
  const debugAssertIndex = Math.max(debugFilterIndex + 1, activeDebugSteps.findIndex((step) => step.atomId === "assert"));
  const completedExecutions = Math.round(selectedTaskTotal * taskProgress / 100);
  const executionStates = Array.from({ length: selectedTaskTotal }, (_, index) => executionStateAt(index, completedExecutions, selectedTaskTotal, running && selectedTaskStatus === "running", selectedTaskStatus !== "passed"));
  const executionCounts = executionStates.reduce((counts, status) => ({ ...counts, [status]: counts[status] + 1 }), { passed: 0, running: 0, queued: 0, product: 0, infra: 0, flaky: 0 } as Record<ExecutionState, number>);
  const selectedExecutionState = executionStates[selectedExecution] ?? "queued";
  const visibleFailureClusters = projectedFailureClusters.length
    ? projectedFailureClusters
    : taskFailureClusters;
  const selectedClusterData = visibleFailureClusters.find(
    (cluster) => cluster.id === selectedCluster
  ) ?? visibleFailureClusters[0];
  const selectedClusterFact = backendFailureClusterPage?.items.find(
    (item) => item.cluster.id === selectedClusterData.id
  );
  const backendSnapshot = backendTaskResult?.resultSnapshot;
  const backendGate = backendTaskResult?.taskGateDecision;
  const resultGateLabel = backendTaskResult
    ? backendGate?.decision ?? "INCONCLUSIVE"
    : selectedTaskStatus === "passed" ? "ACCEPTED" : "REJECTED";
  const resultPassed = resultGateLabel === "ACCEPTED";
  const activeTaskVersions = (selectedTaskRecord?.caseVersionIds ?? []).map((id) => publishedVersions.find((version) => version.id === id)).filter((version): version is NonNullable<typeof version> => Boolean(version));
  const activeManifestCount = selectedTaskRecord?.caseVersionIds?.length ?? Math.max(1, Math.round(selectedTaskTotal / Math.max(1, selectedTaskRoles.length * selectedTaskBrowsers.length)));
  const resultTaskTotal = backendSnapshot?.manifestCount ?? selectedTaskTotal;
  const resultPassCount = backendSnapshot?.verdictCounts.passed
    ?? (resultPassed ? selectedTaskTotal : selectedTaskStatus === "attention" ? selectedTaskRecord?.passed ?? executionCounts.passed : executionCounts.passed);
  const resultProductCount = backendFailureClusterPage
    ? backendFailureClusterPage.items.reduce(
        (total, item) => total + (item.classification?.failureDomain === "PRODUCT" ? item.cluster.affectedCount : 0),
        0
      )
    : resultPassed ? 0 : selectedTaskStatus === "attention" ? 3 : executionCounts.product;
  const resultMethodCount = backendFailureClusterPage
    ? backendFailureClusterPage.items.reduce(
        (total, item) => total + (["TEST_SPEC", "TEST_DATA", "AGENT_AUTOMATION"].includes(item.classification?.failureDomain ?? "") ? item.cluster.affectedCount : 0),
        0
      )
    : resultPassed ? 0 : selectedTaskStatus === "attention" ? 1 : executionCounts.flaky;
  const resultEnvironmentCount = backendFailureClusterPage
    ? backendFailureClusterPage.items.reduce(
        (total, item) => total + (["IDENTITY", "ENVIRONMENT", "INFRASTRUCTURE", "EXTERNAL_DEPENDENCY"].includes(item.classification?.failureDomain ?? "") ? item.cluster.affectedCount : 0),
        0
      )
    : resultPassed ? 0 : selectedTaskStatus === "attention" ? 1 : executionCounts.infra;
  const resultStableRate = backendSnapshot
    ? Math.round(backendSnapshot.trustedPassRate.numerator / backendSnapshot.trustedPassRate.denominator * 1000) / 10
    : selectedTaskTotal ? Math.round(resultPassCount / selectedTaskTotal * 1000) / 10 : 0;
  const resultGateSummary = backendTaskResult
    ? backendGate
      ? backendGate.reasons.length
        ? backendGate.reasons.slice(0, 2).map((reason) => `${reason.code} × ${reason.count}`).join("；")
        : "全部严格门禁条件已满足。"
      : "Snapshot 已冻结，等待显式 Gate 评估。"
    : resultPassed
      ? "未发现新增产品回归，所有发布门禁均已满足。"
      : `发布阈值为 98%，存在 ${resultProductCount} 个新增产品失败。`;
  const resultRecommendationTitle = resultPassed
    ? "建议进入下一道发布门"
    : resultGateLabel === "INCONCLUSIVE"
      ? "等待证据与归因闭环"
      : "阻止本次客户模块发布";
  const resultRecommendationDetail = backendTaskResult
    ? resultGateLabel === "ACCEPTED"
      ? "Snapshot、Evidence、Hygiene、Stability 与 Classification 均满足冻结 Gate Policy。"
      : resultGateSummary
    : resultPassed
      ? "所有执行单元稳定通过，未出现新的产品、测试方法或环境失败信号。"
      : `${resultProductCount} 个失败共享同一响应特征，跨角色复现，判定为产品回归的置信度为 96%。`;
  const matrixGroupLabels = matrixDimension === "role" ? selectedTaskRoles : selectedTaskBrowsers;
  const executionDescriptors = Array.from({ length: selectedTaskTotal }, (_, index) => {
    const version = activeTaskVersions.length ? activeTaskVersions[Math.floor(index / Math.max(1, selectedTaskBrowsers.length)) % activeTaskVersions.length] : undefined;
    const roleIndex = Math.min(Math.max(0, selectedTaskRoles.length - 1), Math.floor(index * selectedTaskRoles.length / Math.max(1, selectedTaskTotal)));
    return { index, version, role: version?.role ?? selectedTaskRoles[roleIndex], browser: selectedTaskBrowsers[index % Math.max(1, selectedTaskBrowsers.length)], caseId: version?.caseId ?? `TC-${1042 + (index % Math.max(1, Math.round(selectedTaskTotal / Math.max(1, selectedTaskRoles.length * selectedTaskBrowsers.length))))}` };
  });
  const selectedDescriptor = executionDescriptors[selectedExecution] ?? executionDescriptors[0];
  const activeExecutionVersion = selectedDescriptor?.version;
  const activeExecutionWorkflow = activeExecutionVersion ? orderWorkflowByGraph(activeExecutionVersion.workflowSnapshot, activeExecutionVersion.edgeSnapshot) : [];
  const replayVersion = activeExecutionVersion ?? selectedLatestVersion;
  const replayWorkflow = replayVersion ? orderWorkflowByGraph(replayVersion.workflowSnapshot, replayVersion.edgeSnapshot) : [];
  const selectedRoleLabel = selectedDescriptor?.role ?? "按用例身份";
  const selectedBrowserLabel = selectedDescriptor?.browser ?? "Chromium";
  const selectedCaseLabel = selectedDescriptor?.caseId ?? "TC-1042";
  const workerLanes = activeTaskVersions.length ? activeTaskVersions.map((version) => `${version.caseName} · ${version.role}`) : ["客户筛选 · 销售", "客户筛选 · 主管", "权限边界 · 客服", "来访绑定 · 销售", "空结果 · 主管", "账号租约 · 客服"];
  const clusterSignal = selectedClusterFact
    ? `${selectedClusterFact.cluster.signal.signalCode} · ${selectedClusterFact.cluster.signal.closureReason}`
    : selectedCluster === "api" ? "GET /api/customers?trackingId=*" : selectedCluster === "selector" ? "DOM semantic role: customer-filter" : "AUTH_SESSION_EXPIRED · service pool";
  const clusterConclusion = selectedClusterFact
    ? selectedClusterFact.classification?.hypothesis ?? "当前失败簇尚未形成可发布的归因结论。"
    : selectedCluster === "api" ? "相同接口在销售与主管角色下持续返回 502，接口重放仍可复现。" : selectedCluster === "selector" ? "页面筛选入口的语义标签发生漂移，业务接口与数据断言均正常。" : "失败集中在客服账号池，刷新身份租约后执行单元可以稳定通过。";
  const clusterNodes = selectedClusterFact
    ? [
        ...selectedClusterFact.cluster.affectedUnitResolutionRevisionIds.slice(0, 3).map((id) => `UNIT · ${id.slice(0, 8)}`),
        selectedClusterFact.cluster.signal.outcomeClass,
        selectedClusterFact.cluster.signal.effectiveVerdict
      ]
    : selectedCluster === "api"
    ? ["销售 · Chromium", "主管 · Chromium", "客户列表", "预发环境", "queryCustomers"]
    : selectedCluster === "selector"
      ? ["销售 · Chromium", "客户列表", "筛选面板", "semantic role", "TC-1042"]
      : ["客服 · Chromium", "身份钱包", "登录回调", "预发环境", "service pool"];
  const clusterEvidence = selectedClusterFact
    ? selectedClusterFact.classification?.supportingEvidenceRefs.slice(0, 4).map((evidence) => `${evidence.kind} · ${evidence.refId.slice(0, 8)}`)
      ?? ["等待人工补充归因证据"]
    : selectedCluster === "api"
    ? ["相同接口与状态码", "跨两个角色复现", "接口重放仍然失败", "页面操作路径一致"]
    : selectedCluster === "selector"
      ? ["业务接口返回正常", "DOM 语义标签已变化", "视觉入口仍然存在", "旧定位规则无法命中"]
      : ["仅客服账号池受影响", "刷新租约后恢复", "业务接口稳定通过", "新浏览器上下文可复现恢复"];
  const clusterAction = selectedClusterFact
    ? selectedClusterFact.classification?.failureDomain === "PRODUCT"
      ? "创建产品缺陷"
      : ["TEST_SPEC", "TEST_DATA", "AGENT_AUTOMATION"].includes(selectedClusterFact.classification?.failureDomain ?? "")
        ? "提交方法修订"
        : "创建环境事件"
    : selectedCluster === "api" ? "创建产品缺陷" : selectedCluster === "selector" ? "提交方法修订" : "创建环境事件";
  const insightTerrain = fallbackInsightTerrain.map((fallback, index) => {
    const item = insightBrief?.terrain[index];
    return item
      ? {
          label: item.label,
          rate: formatInsightRate(item.trustedPassRate.basisPoints, fallback.rate)
        }
      : fallback;
  });
  const insightExecutionCount = (
    insightBrief?.current.executionUnitCount ?? 1_284
  ).toLocaleString("zh-CN");
  const insightTrustedPassRate = formatInsightRate(
    insightBrief?.current.trustedPassRate.basisPoints,
    "96.8%"
  );
  const insightTrustedPassDelta = formatInsightDelta(
    insightBrief?.deltas.trustedPassRate,
    "较上周期 +1.4%"
  );
  const insightMethodHealth = insightBrief?.current.methodHealthRate.basisPoints == null
    ? "94"
    : String(Math.round(insightBrief.current.methodHealthRate.basisPoints / 100));
  const insightMethodPopulation = insightBrief
    ? `${insightBrief.current.methodHealthRate.denominator} 个 ExecutionUnit`
    : "36 条已发布方法";
  const insightRiskTitle = insightBrief?.activeRisk?.taskPlanName
    ?? "客户权限发布门禁";
  const insightRiskDetail = insightBrief?.activeRisk
    ? `${insightBrief.activeRisk.reasonCount} 条门禁原因，当前判断为 ${insightBrief.activeRisk.gateDecision}。`
    : "5 个失败已拆分为产品、测试方法与环境信号。";
  const latestRiskTask = baseTaskRuns.find(
    (task) => task.backendId === insightBrief?.activeRisk?.taskRunId
  ) ?? baseTaskRuns.find((task) => task.status === "attention")
    ?? taskRuns[1];

  useEffect(() => {
    if (
      !projectedFailureClusters.length
      || projectedFailureClusters.some((cluster) => cluster.id === selectedCluster)
    ) {
      return;
    }
    setSelectedCluster(projectedFailureClusters[0].id);
  }, [projectedFailureClusters, selectedCluster]);

  useEffect(() => {
    if (!running || liveMode !== "task" || (view !== "launch" && view !== "live")) return;
    if (taskProgress >= 100) {
      setRunning(false);
      return;
    }
    const timer = window.setTimeout(() => setTaskProgress((progress) => Math.min(100, progress + 1)), 1900);
    return () => window.clearTimeout(timer);
  }, [taskProgress, running, view, liveMode]);

  useEffect(() => {
    if (!running || liveMode !== "single" || view !== "live") return;
    if (runStep >= activeDebugSteps.length) {
      setRunning(false);
      if (debugContext) {
        setTestCases((cases) => cases.map((item) => item.id === debugContext.caseId && item.draftRevision === debugContext.draftRevision ? { ...item, lastDebugRevision: debugContext.draftRevision } : item));
        setToast(`Draft r${debugContext.draftRevision} 调试通过，现在可以发布`);
      }
      return;
    }
    const timer = window.setTimeout(() => setRunStep((step) => step + 1), 1100);
    return () => window.clearTimeout(timer);
  }, [activeDebugSteps.length, debugContext, runStep, running, view, liveMode]);

  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(""), 2600);
    return () => window.clearTimeout(timer);
  }, [toast]);

  function navigate(next: ViewId) {
    if (next === "live") setLiveMode("task");
    if (next === "launch") {
      const focusTask = scheduledTask?.status === "running" ? scheduledTask : activeTask;
      setSelectedTaskId(focusTask.id);
      setSelectedTaskStatus(focusTask.status);
      setSelectedTaskTotal(focusTask.executions);
      setSelectedTaskMatrix(focusTask.matrix);
      setSelectedTaskRoles(focusTask.roles);
      setSelectedTaskBrowsers(focusTask.browsers);
      setTaskProgress(focusTask.progress);
      setRunning(true);
    }
    setView(next);
    setMobileNav(false);
  }

  function openCase(caseId: string) {
    const target = testCases.find((item) => item.id === caseId);
    setSelectedCaseId(caseId);
    if (target?.workflow[0]) setSelectedNode(target.workflow[0].id);
    setView("cases");
    setMobileNav(false);
  }

  function updateDraft(source: "AI" | "人工", addCleanup = false) {
    setTestCases((cases) => cases.map((item) => {
      if (item.id !== selectedCaseId) return item;
      const hasCleanup = item.workflow.some((node) => node.phase === "cleanup");
      if (source === "人工") {
        const workflow = item.workflow.map((node) => node.id === selectedNode ? { ...node, name: node.name.includes("已调") ? node.name : `${node.name} · 已调`, output: `${node.output} · r${item.draftRevision + 1}` } : node);
        return { ...item, draftRevision: item.draftRevision + 1, updatedBy: source, workflow };
      }

      const nextRevision = item.draftRevision + 1;
      const lastAssert = [...item.workflow].reverse().find((node) => node.phase === "assert");
      if (addCleanup && !hasCleanup) {
        const cleanupNode: WorkflowNode = { id: `n-cleanup-${nextRevision}`, atomId: "cleanup", name: "清理测试数据", phase: "cleanup", kind: "API", evidence: "204 No Content", output: "cleanupResult", position: { x: 984, y: 238 } };
        const nextEdge: WorkflowEdge | null = lastAssert ? { id: `e-${lastAssert.id}-${cleanupNode.id}`, source: lastAssert.id, target: cleanupNode.id, sourcePort: "result", targetPort: "result", label: "result" } : null;
        return { ...item, draftRevision: nextRevision, updatedBy: source, workflow: [...item.workflow, cleanupNode], edges: nextEdge ? [...item.edges, nextEdge] : item.edges };
      }

      const existingNegative = item.workflow.find((node) => node.id.includes("negative"));
      if (existingNegative) {
        const workflow = item.workflow.map((node) => node.id === existingNegative.id ? { ...node, name: `验证${item.role}负向边界 · AI refined`, evidence: "permission denied · deterministic" } : node);
        return { ...item, draftRevision: nextRevision, updatedBy: source, workflow };
      }

      const cleanupNode = item.workflow.find((node) => node.phase === "cleanup");
      const negativeCount = item.workflow.filter((node) => node.id.includes("negative")).length;
      const negativeNode: WorkflowNode = { id: `n-negative-${nextRevision}`, atomId: "assert", name: `验证${item.role}负向边界`, phase: "assert", kind: "Assert", evidence: "permission denied", output: "negativeResult", position: { x: 804, y: Math.min(408, 286 + negativeCount * 82) } };
      const insertAt = cleanupNode ? item.workflow.findIndex((node) => node.id === cleanupNode.id) : item.workflow.length;
      const workflow = [...item.workflow.slice(0, insertAt), negativeNode, ...item.workflow.slice(insertAt)];
      const edgesWithoutDirectCleanup = cleanupNode && lastAssert ? item.edges.filter((edge) => !(edge.source === lastAssert.id && edge.target === cleanupNode.id)) : item.edges;
      const edges = lastAssert ? [...edgesWithoutDirectCleanup, { id: `e-${lastAssert.id}-${negativeNode.id}`, source: lastAssert.id, target: negativeNode.id, sourcePort: "result", targetPort: "result", label: "result" }] : edgesWithoutDirectCleanup;
      if (cleanupNode) edges.push({ id: `e-${negativeNode.id}-${cleanupNode.id}`, source: negativeNode.id, target: cleanupNode.id, sourcePort: "result", targetPort: "result", label: "result" });
      return { ...item, draftRevision: nextRevision, updatedBy: source, workflow, edges };
    }));
    setToast(source === "AI" ? "AI Patch 已写入同一份 WorkflowDraft" : "人工改动已写入当前 WorkflowDraft");
  }

  function moveWorkflowNode(nodeId: string, position: CanvasPoint, commit: boolean) {
    setTestCases((cases) => cases.map((item) => item.id === selectedCaseId ? {
      ...item,
      workflow: item.workflow.map((node) => node.id === nodeId ? { ...node, position } : node)
    } : item));
    if (commit) setToast(`画布布局已保存 · 执行 Draft 仍为 r${selectedCase.draftRevision}`);
  }

  function autoLayoutWorkflow() {
    setTestCases((cases) => cases.map((item) => {
      if (item.id !== selectedCaseId) return item;
      const ordered = orderWorkflowByGraph(item.workflow, item.edges);
      const levelById = new Map<string, number>();
      ordered.forEach((node) => {
        const parents = item.edges.filter((edge) => edge.target === node.id).map((edge) => levelById.get(edge.source) ?? 0);
        levelById.set(node.id, parents.length ? Math.max(...parents) + 1 : 0);
      });
      const groups = new Map<number, string[]>();
      ordered.forEach((node) => {
        const level = levelById.get(node.id) ?? 0;
        groups.set(level, [...(groups.get(level) ?? []), node.id]);
      });
      const workflow = item.workflow.map((node) => {
        const level = levelById.get(node.id) ?? 0;
        const peers = groups.get(level) ?? [node.id];
        const row = peers.indexOf(node.id);
        const spacing = Math.min(138, 420 / Math.max(1, peers.length));
        return { ...node, position: { x: Math.min(990, 54 + level * 184), y: Math.max(32, 270 - (peers.length - 1) * spacing / 2 + row * spacing) } };
      });
      return { ...item, workflow };
    }));
    setToast(`依赖图已自动布局 · 执行 Draft 仍为 r${selectedCase.draftRevision}`);
  }

  function connectWorkflowNodes(source: string, target: string) {
    const active = testCases.find((item) => item.id === selectedCaseId);
    if (!active) return;
    if (active.edges.some((edge) => edge.source === source && edge.target === target)) {
      setToast("这两个端口已经连接");
      return;
    }
    if (wouldCreateCycle(active.edges, source, target)) {
      setToast("无法创建环形依赖，请调整连接方向");
      return;
    }
    const sourceNode = active.workflow.find((node) => node.id === source);
    const targetNode = active.workflow.find((node) => node.id === target);
    if (!sourceNode || !targetNode) return;
    const compatiblePort = findCompatiblePort(sourceNode, targetNode, active.edges);
    if (!compatiblePort) {
      setToast(`${sourceNode.name} 与 ${targetNode.name} 没有可匹配的空闲类型端口`);
      return;
    }
    setTestCases((cases) => cases.map((item) => {
      if (item.id !== selectedCaseId) return item;
      const edge: WorkflowEdge = { id: `e-${source}-${target}-r${item.draftRevision + 1}`, source, target, ...compatiblePort, label: compatiblePort.targetPort };
      return { ...item, draftRevision: item.draftRevision + 1, updatedBy: "人工", edges: [...item.edges, edge] };
    }));
    setToast(`${sourceNode.name}.${compatiblePort.sourcePort} → ${targetNode.name}.${compatiblePort.targetPort}`);
  }

  function deleteWorkflowEdge(edgeId: string) {
    const edge = selectedCase.edges.find((item) => item.id === edgeId);
    if (!edge) return;
    setTestCases((cases) => cases.map((item) => item.id === selectedCaseId ? { ...item, draftRevision: item.draftRevision + 1, updatedBy: "人工", edges: item.edges.filter((candidate) => candidate.id !== edgeId) } : item));
    setToast(`连线 ${edge.label} 已删除 · Draft r${selectedCase.draftRevision + 1}`);
  }

  function applyAsset(assetId: string) {
    const asset = visibleWorkflowAssets.find((item) => item.id === assetId);
    if (!asset) return;
    const instanceId = `${asset.id}-${selectedCase.draftRevision + 1}`;
    setWorkflowMode("manual");
    setTestCases((cases) => cases.map((item) => {
      if (item.id !== selectedCaseId) return item;
      const phase: WorkflowPhase = assetId === "asset-data" ? "setup" : assetId === "asset-login" ? "identity" : assetId === "asset-filter" ? "execute" : assetId === "asset-assert" ? "assert" : "cleanup";
      const replacedNodes = assetId === "asset-cleanup" ? item.workflow.filter((node) => node.phase === "cleanup") : item.workflow.filter((node) => node.atomId === asset.id || (asset.atoms.includes(node.atomId) && !node.id.includes("negative")));
      const replacedIds = new Set(replacedNodes.map((node) => node.id));
      const anchor = replacedNodes[0] ?? [...item.workflow].reverse().find((node) => node.phase === "assert");
      const averagePosition = replacedNodes.length ? { x: replacedNodes.reduce((sum, node) => sum + node.position.x, 0) / replacedNodes.length, y: replacedNodes.reduce((sum, node) => sum + node.position.y, 0) / replacedNodes.length } : { x: Math.min(984, (anchor?.position.x ?? 740) + 210), y: anchor?.position.y ?? 230 };
      const assetNode: WorkflowNode = { id: instanceId, atomId: asset.id, name: asset.name, phase, kind: "Subflow", evidence: `${asset.version} locked`, output: getNodeOutputs({ id: instanceId, atomId: asset.id, name: asset.name, phase, kind: "Subflow", evidence: "", output: "result", position: averagePosition }).join(" · "), position: averagePosition };

      let workflow: WorkflowNode[];
      let edges: WorkflowEdge[];
      if (replacedNodes.length) {
        const firstIndex = item.workflow.findIndex((node) => replacedIds.has(node.id));
        workflow = item.workflow.flatMap((node, index) => index === firstIndex ? [assetNode] : replacedIds.has(node.id) ? [] : [node]);
        edges = item.edges.flatMap((edge) => {
          const sourceReplaced = replacedIds.has(edge.source);
          const targetReplaced = replacedIds.has(edge.target);
          if (sourceReplaced && targetReplaced) return [];
          const nextEdge = { ...edge, id: `asset-${assetNode.id}-${edge.id}`, source: sourceReplaced ? assetNode.id : edge.source, target: targetReplaced ? assetNode.id : edge.target };
          if (sourceReplaced && !getNodeOutputs(assetNode).includes(nextEdge.sourcePort)) return [];
          if (targetReplaced && !getNodeInputs(assetNode).includes(nextEdge.targetPort)) return [];
          return [nextEdge];
        });
      } else {
        workflow = [...item.workflow, assetNode];
        const sourceNode = [...item.workflow].reverse().find((node) => getNodeOutputs(node).includes("result"));
        edges = sourceNode ? [...item.edges, { id: `e-${sourceNode.id}-${assetNode.id}`, source: sourceNode.id, target: assetNode.id, sourcePort: "result", targetPort: "result", label: "result" }] : item.edges;
      }
      const uniqueEdges = edges.filter((edge, index, all) => all.findIndex((candidate) => candidate.source === edge.source && candidate.target === edge.target && candidate.sourcePort === edge.sourcePort && candidate.targetPort === edge.targetPort) === index);
      return { ...item, draftRevision: item.draftRevision + 1, updatedBy: "人工", workflow, edges: uniqueEdges };
    }));
    setSelectedNode(instanceId);
    setToast(`${asset.name} 已实例化并接续类型端口 · Draft r${selectedCase.draftRevision + 1}`);
  }

  function publishCurrentCase() {
    if (!graphValidation.valid) {
      setToast(`编排图未闭合：${graphValidation.issues[0]}`);
      return;
    }
    if (!currentDraftDebugged) {
      setToast("请先让当前 Draft 完成一次实时调试");
      return;
    }
    if (currentDraftPublished) {
      setToast(`${selectedLatestVersion.version} 已是当前草稿的冻结版本`);
      return;
    }
    const latestNumber = selectedCase.versions[0]?.version.replace("v", "").split(".").map(Number) ?? [0, 9];
    const publishedLabel = `v${latestNumber[0]}.${(latestNumber[1] ?? 0) + 1}`;
    setTestCases((cases) => cases.map((item) => {
      if (item.id !== selectedCaseId) return item;
      const version: CaseVersion = { id: `${item.id}@${publishedLabel}`, version: publishedLabel, caseName: item.name, role: item.role, sourceRevision: item.draftRevision, publishedAt: "刚刚", workflowSnapshot: cloneWorkflow(item.workflow), edgeSnapshot: cloneEdges(item.edges) };
      return { ...item, versions: [version, ...item.versions] };
    }));
    setToast(`${selectedCase.id} · ${publishedLabel} 已冻结，Task 现在可以选择它`);
  }

  function toggleTaskVersion(versionId: string) {
    setTaskVersionIds((ids) => ids.includes(versionId) ? ids.filter((id) => id !== versionId) : [...ids, versionId]);
  }

  function launchTask() {
    const total = plannedExecutions || 120;
    const matrix = `${selectedCaseCount || 1} CaseVersion × ${taskBrowsers.length || 1} 浏览器`;
    const newTask: TaskRun = { id: "TASK-2049", name: "R26.07 自定义回归", trigger: "手动 · 刚刚", status: "running", progress: 6, executions: total, passed: 0, failed: 0, eta: "计算中", matrix, roles: caseBoundRoles, browsers: taskBrowsers, caseVersionIds: [...taskVersionIds] };
    setScheduledTask(newTask);
    setSelectedTaskId(newTask.id);
    setSelectedTaskStatus("running");
    setSelectedTaskTotal(total);
    setSelectedTaskMatrix(matrix);
    setSelectedTaskRoles(caseBoundRoles);
    setSelectedTaskBrowsers(taskBrowsers);
    setSelectedExecution(0);
    setTaskProgress(6);
    setRunning(true);
    setLiveMode("task");
    setTaskPanel("center");
    setView("live");
    setToast(`${total} 个 Execution 已进入调度通道`);
  }

  function createScheduledTask() {
    const total = plannedExecutions || 120;
    const matrix = `${selectedCaseCount || 1} CaseVersion × ${taskBrowsers.length || 1} 浏览器`;
    const trigger = taskTrigger.includes("每日") ? `定时 · ${taskTrigger}` : taskTrigger.includes("CI") ? "CI · 等待 Webhook" : "发布事件 · 等待部署";
    setScheduledTask({ id: "TASK-2049", name: "R26.07 自定义回归", trigger, status: "queued", progress: 0, executions: total, passed: 0, failed: 0, eta: taskTrigger.includes("每日") ? "21:30" : "等待触发", matrix, roles: caseBoundRoles, browsers: taskBrowsers, caseVersionIds: [...taskVersionIds] });
    setTaskPanel("center");
    setTaskViewMode("list");
    setToast(`${total} 个 Execution 已按“${taskTrigger}”创建调度`);
  }

  function launchSingleRun() {
    if (!graphValidation.valid) {
      setToast(`无法调试：${graphValidation.issues[0]}`);
      return;
    }
    const edges = cloneEdges(selectedCase.edges);
    const nodes = cloneWorkflow(selectedCase.workflow);
    setDebugContext({ caseId: selectedCase.id, caseName: selectedCase.name, draftRevision: selectedCase.draftRevision, steps: orderWorkflowByGraph(nodes, edges), edges });
    setRunStep(0);
    setRunning(true);
    setLiveMode("single");
    setView("live");
    setToast(`正在调试 ${selectedCase.id} · Draft r${selectedCase.draftRevision}`);
  }

  function replaySelectedExecution() {
    setLiveMode("task");
    setRunning(false);
    setView("live");
    setToast(`正在回放 ${selectedTaskId} · EXE-${String(selectedExecution + 1).padStart(3, "0")} 的冻结证据`);
  }

  function openTask(task: TaskRun) {
    setSelectedTaskId(task.id);
    setSelectedTaskStatus(task.status);
    setSelectedTaskTotal(task.executions);
    setSelectedTaskMatrix(task.matrix);
    setSelectedTaskRoles(task.roles);
    setSelectedTaskBrowsers(task.browsers);
    setSelectedExecution(0);
    setTaskProgress(task.progress);
    setRunning(task.status === "running");
    setLiveMode("task");
    if (task.status === "attention" || task.status === "passed") navigate("results");
    else navigate("live");
  }

  function toggleOption(value: string, selected: string[], update: (values: string[]) => void) {
    if (selected.includes(value)) {
      if (selected.length > 1) update(selected.filter((item) => item !== value));
      return;
    }
    update([...selected, value]);
  }

  const spaceView = (
    <section className="scene space-scene">
      <SceneIntro
        eyebrow="CRM · R26.07 · QUALITY SPACE"
        title="测试，不再是一张报表。"
        description="让身份、数据、Agent 与证据在同一个任务空间中自然连接。"
        action={<><button className="ghost-action" onClick={() => navigate("insights")}>查看质量轨迹 <ArrowUpRight size={16} /></button><button className="black-action" onClick={() => openCase("TC-1042")}><Play size={15} />打开用例工作室</button></>}
      />

      <div className="space-bento">
        <article className="space-hero">
          <div className="card-heading"><div><span>TEST SPACE / 01</span><h2>客户筛选核心旅程</h2></div><StatusPill tone="good"><Radio size={11} />运行健康</StatusPill></div>
          <TestOrbit />
          <div className="hero-foot"><span>6 个原子</span><span>2 个并行分支</span><span>销售身份</span><button onClick={() => openCase("TC-1042")}>进入用例 <ArrowRight size={14} /></button></div>
        </article>

        <article className="run-deck">
          <div className="card-heading"><div><span>AGENT QUEUE</span><h2>运行牌组</h2></div><button className="round-add" onClick={() => navigate("launch")}><Plus size={17} /></button></div>
          <div className="stacked-runs">
            <button className="run-card run-card-back" onClick={() => navigate("results")}><span>权限矩阵</span><b>24 / 24</b><small>已完成</small></button>
            <button className="run-card run-card-mid" onClick={() => navigate("results")}><span>来访单冒烟</span><b>10 / 12</b><small>2 个异常</small></button>
            <button className="run-card run-card-front" onClick={() => navigate("live")}><div><span>RUN-2048</span><StatusPill tone="dark">LIVE</StatusPill></div><h3>客户核心回归</h3><p>Agent 正在定位筛选入口</p><div className="deck-progress"><i /></div><footer><span>04:18</span><strong>4 / 6</strong></footer></button>
          </div>
        </article>

        <article className="identity-pulse" onClick={() => navigate("identities")}>
          <div className="card-heading"><div><span>IDENTITY WALLET</span><h2>身份池</h2></div><StatusPill tone="good">{totalIdentityAvailable} 可用</StatusPill></div>
          <div className="avatar-orbit"><span>销</span><span>管</span><span>客</span><span>管</span><i>+20</i></div>
          <div className="pulse-line"><i /><i /><i /><i /><i /></div>
          <p>{totalIdentityLeased} 个身份正在被执行旅程租用</p>
        </article>

        <article className="release-card">
          <div className="card-heading"><div><span>RELEASE ORBIT</span><h2>R26.07</h2></div><b>73%</b></div>
          <div className="release-bars"><i style={{ height: "34%" }} /><i style={{ height: "55%" }} /><i style={{ height: "48%" }} /><i style={{ height: "72%" }} /><i style={{ height: "64%" }} /><i className="active" style={{ height: "88%" }} /></div>
          <footer><span>156 条旅程</span><span>7 月 18 日发布</span></footer>
        </article>

        <article className="risk-lens" onClick={() => navigate("insights")}>
          <div className="risk-glow"><CircleAlert size={20} /></div>
          <div><span>RISK LENS</span><h2>2 个风险簇正在聚合</h2><p>客户查询接口 · 客服登录态</p></div>
          <ArrowUpRight size={18} />
        </article>
      </div>

      <div className="journey-strip">
        <div className="strip-title"><span>最近旅程</span><button onClick={() => navigate("insights")}>回看全部 <ChevronRight size={14} /></button></div>
        {[{ id: "2048", name: "销售筛选客户", status: "执行中", tone: "violet", time: "04:18" }, { id: "2047", name: "主管团队视图", status: "通过", tone: "mint", time: "08:21" }, { id: "2046", name: "来访单绑定", status: "2 个异常", tone: "coral", time: "05:36" }].map((run) => <button className={`journey-ticket ticket-${run.tone}`} key={run.id} onClick={() => navigate(run.status === "执行中" ? "live" : "results")}><span>RUN-{run.id}</span><strong>{run.name}</strong><small>{run.status}</small><b>{run.time}</b><ArrowUpRight size={15} /></button>)}
      </div>
    </section>
  );

  const identitiesView = (
    <section className="scene identities-scene">
      <SceneIntro eyebrow="IDENTITY WALLET" title="把角色放进场景，而不是填进表格。" description="每张身份卡都携带权限、租约和登录状态；拖入场景即可形成角色测试矩阵。" action={<button className="black-action" onClick={() => setToast("身份创建向导已打开")}><Plus size={15} />创建身份</button>} />
      <div className="identity-stage">
        <div className="identity-wallet">
          <div className="wallet-label"><span>TEST IDENTITIES</span><b>04</b></div>
          <div className="identity-stack">
            {identityCards.map((item, index) => <button key={item.id} className={`identity-card identity-${item.color} identity-index-${index} ${selectedIdentity === item.id ? "selected" : ""}`} onClick={() => setSelectedIdentity(item.id)}><div><span>{item.role}身份</span><Fingerprint size={20} /></div><strong>{item.account}</strong><p>{item.available} / {item.total} 可用</p><footer><span>{item.environment}</span><b>{String(index + 1).padStart(2, "0")}</b></footer></button>)}
          </div>
        </div>
        <aside className="identity-passport">
          <div className="passport-top"><div className={`passport-avatar identity-${identity.color}`}>{identity.role.slice(0, 1)}</div><div><span>SELECTED IDENTITY</span><h2>{identity.role} · {identity.account}</h2></div><StatusPill tone={identity.available ? "good" : "warn"}>{identity.available ? "READY" : "WAIT"}</StatusPill></div>
          <div className="capacity-orbit"><div><strong>{identity.available}</strong><span>可用账号</span></div><i style={{ "--capacity": `${Math.round(identity.available / Math.max(identity.total, 1) * 100)}%` } as React.CSSProperties} /></div>
          <div className="passport-section"><span>权限钥匙</span><div className="power-keys">{identity.powers.map((power) => <button key={power}><KeyRound size={13} />{power}</button>)}</div></div>
          <div className="lease-thread"><span><i />空闲 {identity.available}</span><span><i />租用中 {identity.leased}</span><span><i />健康度 {identity.health}%</span></div>
          <button className="passport-action" onClick={() => navigate("compose")}>将身份放入场景 <ArrowRight size={15} /></button>
        </aside>
      </div>
      <div className="lease-ribbon"><span>正在租用</span>{["sales-11 · RUN-2048", "manager-02 · RUN-2047", "sales-07 · RUN-2045"].map((lease) => <button key={lease}><span className="live-pin" />{lease}<ChevronRight size={13} /></button>)}<button className="ribbon-more">查看租约历史</button></div>
    </section>
  );

  const atomsView = (
    <section className="scene atoms-scene">
      <SceneIntro eyebrow="ATOMIC LAB" title="业务能力，像积木一样有形。" description="输入和输出决定它们如何吸附；版本、健康和使用范围都浓缩在一张卡面上。" action={<button className="black-action" onClick={() => setToast("原子创建向导已打开")}><Plus size={15} />制造原子</button>} />
      <div className="atom-lab">
        <div className="atom-toolbar"><label><Search size={15} /><input placeholder="描述你需要的能力，例如：创建一个已绑定来访单的客户" /></label><button><WandSparkles size={16} />让 AI 推荐组合</button></div>
        <div className="atom-field">
          <div className="periodic-grid">
            {visibleAtomSpecs.map((item, index) => <button key={item.id} className={`atom-tile atom-${item.type} atom-pos-${index} ${atom.id === item.id ? "selected" : ""}`} onClick={() => setSelectedAtom(item.id)}><span>{item.key}</span><Atom size={20} /><strong>{item.name}</strong><footer><small>v{item.version}</small><b>{item.health}%</b></footer></button>)}
            <button className="atom-empty" onClick={() => setToast("开始制造一个新的原子组件")}><Plus size={20} /><span>空位</span></button>
          </div>
          <div className="assembly-shelf"><span>快速装配槽</span><div><i><Fingerprint size={15} /></i><ArrowRight size={14} /><i><Database size={15} /></i><ArrowRight size={14} /><i><Bot size={15} /></i><ArrowRight size={14} /><i><ShieldCheck size={15} /></i></div><button onClick={() => navigate("compose")}>打开为场景</button></div>
        </div>
        <aside className={`atom-lens lens-${atom.type}`}>
          <div className="lens-index">A/{visibleAtomSpecs.findIndex((item) => item.id === atom.id) + 1}</div>
          <div className="lens-icon"><Atom size={27} /></div>
          <span>{atom.key} · v{atom.version}</span><h2>{atom.name}</h2><p>{atom.description}</p>
          <div className="port-contract"><div><span>输入端口</span>{atom.inputs.map((input) => <code key={input}><i />{input}</code>)}</div><div><span>输出端口</span>{atom.outputs.map((output) => <code key={output}>{output}<i /></code>)}</div></div>
          <div className="lens-health"><span>运行健康</span><div><i style={{ width: `${atom.health}%` }} /></div><b>{atom.health}%</b></div>
          <button onClick={() => navigate("compose")}>拿去组装 <ArrowRight size={15} /></button>
        </aside>
      </div>
    </section>
  );

  const composeView = (
    <section className="scene compose-scene">
      <SceneIntro eyebrow="ORCHESTRATION ASSET LIBRARY" title="成熟路线，应该成为可复用的资产。" description="原子负责单一能力，资产封装可复用片段；真正可运行的编排始终归属于某一条测试用例。" action={<button className="black-action" onClick={() => navigate("cases")}><ArrowRight size={15} />回到用例工作室</button>} />
      <div className="asset-library-stage">
        <div className="asset-context-bar"><div><span>当前目标用例</span><strong>{selectedCase.id} · {selectedCase.name}</strong><small>加入资产会写入同一份 Draft r{selectedCase.draftRevision}</small></div><StatusPill tone={getCaseState(selectedCase).tone}>{getCaseState(selectedCase).label}</StatusPill><button onClick={() => navigate("cases")}>更换目标 <ChevronRight size={14} /></button></div>
        <div className="asset-constellation">
          <div className="asset-section-title"><div><span>REUSABLE CONSTELLATION</span><h2>编排资产星图</h2></div><p>由多个原子封装而成，可被不同用例引用，但不会脱离用例单独运行。</p></div>
          <div className="asset-grid">{visibleWorkflowAssets.map((assetItem, index) => <article key={assetItem.id} className={`asset-card asset-${assetItem.tone} asset-card-${index} ${selectedAsset.id === assetItem.id ? "selected" : ""}`}><button className="asset-card-main" onClick={() => setSelectedAssetId(assetItem.id)}><span>{assetItem.category}</span><Component size={20} /><strong>{assetItem.name}</strong><small>{assetItem.version} · {assetItem.refs} 个用例引用</small><div>{assetItem.atoms.length ? assetItem.atoms.map((atomId) => <i key={atomId}>{visibleAtomSpecs.find((item) => item.id === atomId)?.name.slice(0, 1)}</i>) : <i>清</i>}<b>{assetItem.health}%</b></div></button><button className="asset-apply" onClick={() => applyAsset(assetItem.id)}>加入当前用例 <Plus size={13} /></button></article>)}</div>
        </div>
        <aside className="asset-inspector"><div className="asset-inspector-head"><Box size={19} /><StatusPill tone="good">HEALTHY</StatusPill></div><span>SELECTED ASSET</span><h2>{selectedAsset.name}</h2><p>资产只保存节点、端口映射与策略默认值。加入用例后会生成独立实例，再由 AI 或人工继续修改。</p><div className="asset-contract"><span>组成原子</span>{selectedAsset.atoms.length ? selectedAsset.atoms.map((atomId) => <code key={atomId}><i />{visibleAtomSpecs.find((item) => item.id === atomId)?.key}</code>) : <code><i />cleanup.compensation</code>}</div><div className="ownership-ladder"><span><Atom size={13} />Atomic</span><ArrowRight size={12} /><span className="active"><Component size={13} />Asset</span><ArrowRight size={12} /><span><GitBranch size={13} />Case Draft</span><ArrowRight size={12} /><span><BadgeCheck size={13} />Version</span></div><button onClick={() => applyAsset(selectedAsset.id)}>套用到 {selectedCase.id} <ArrowRight size={14} /></button></aside>
      </div>
    </section>
  );

  const casesView = (
    <section className="scene cases-scene">
      <SceneIntro eyebrow="CASE WORKBENCH" title="用例，才是编排真正的容器。" description="AI 与人工共同编辑同一份 WorkflowDraft；调试草稿，发布快照，再交给 Task 锁定执行。" action={<button className="black-action" onClick={() => setToast("新用例向导已打开：先写目标，再生成空白 Draft")}><Plus size={15} />新建用例</button>} />
      <div className="case-workbench">
        <aside className="case-rail"><div className="case-rail-title"><span>TEST CASES</span><b>{testCases.length}</b></div>{testCases.map((item) => { const state = getCaseState(item); return <button key={item.id} className={selectedCase.id === item.id ? "selected" : ""} onClick={() => { setSelectedCaseId(item.id); if (item.workflow[0]) setSelectedNode(item.workflow[0].id); }}><span>{item.id}</span><strong>{item.name}</strong><small>{item.role} · Draft r{item.draftRevision}</small><i className={`case-state-dot state-${state.tone}`} /><em>{state.label}</em></button>; })}<button className="case-rail-add" onClick={() => setToast("新用例将创建独立的 WorkflowDraft")}><Plus size={15} />创建空白用例</button></aside>
        <div className="case-workspace">
          <div className="case-version-bar"><div><span>{selectedCase.id}</span><h2>{selectedCase.name}</h2><small>Draft r{selectedCase.draftRevision} · {selectedCase.updatedBy} 最后编辑</small></div><div className="version-line"><StatusPill tone={getCaseState(selectedCase).tone}>{getCaseState(selectedCase).label}</StatusPill><span>{selectedLatestVersion ? `上次发布 ${selectedLatestVersion.version}` : "尚未发布"}</span></div></div>
          <div className="workflow-mode-switch"><button className={workflowMode === "ai" ? "active" : ""} onClick={() => setWorkflowMode("ai")}><Sparkles size={14} />AI 编排</button><button className={workflowMode === "manual" ? "active" : ""} onClick={() => setWorkflowMode("manual")}><Grip size={14} />人工编排</button><span>共同编辑 Draft r{selectedCase.draftRevision}</span></div>
          <div className={`case-intent-panel mode-${workflowMode}`}><div>{workflowMode === "ai" ? <BrainCircuit size={18} /> : <MousePointer2 size={18} />}<span>{workflowMode === "ai" ? "AI COPILOT · PATCH MODE" : "MANUAL CANVAS · DIRECT MODE"}</span></div><p>{workflowMode === "ai" ? selectedCase.intent : "拖拽只保存画布布局；调整端口连线与节点参数，才会形成新的 WorkflowDraft revision。"}</p><button onClick={() => workflowMode === "ai" ? updateDraft("AI", true) : updateDraft("人工")}>{workflowMode === "ai" ? <>生成编排 Patch <WandSparkles size={14} /></> : <>保存节点参数 <Check size={14} /></>}</button></div>
          <div className="workflow-phase-ribbon">{(["setup", "identity", "execute", "assert", "cleanup"] as WorkflowPhase[]).map((phase) => <span key={phase} className={selectedCase.workflow.some((node) => node.phase === phase) ? "filled" : ""}><i />{phase}</span>)}</div>
          <WorkflowCanvas nodes={selectedCase.workflow} edges={selectedCase.edges} canvasKey={selectedCase.id} selectedNodeId={selectedNode} draftRevision={selectedCase.draftRevision} editable={workflowMode === "manual"} onSelectNode={setSelectedNode} onNodePositionChange={moveWorkflowNode} onConnect={connectWorkflowNodes} onDeleteEdge={deleteWorkflowEdge} onAutoLayout={autoLayoutWorkflow} onOpenAssets={() => navigate("compose")} />
          <div className="case-action-dock"><button onClick={() => navigate("compose")}><Component size={15} />加入资产</button><button className="debug-action" onClick={launchSingleRun}><Play size={15} />{graphValidation.valid ? `实时调试 Draft r${selectedCase.draftRevision}` : "先修复依赖图"}</button><button className="publish-action" disabled={!graphValidation.valid || !currentDraftDebugged || currentDraftPublished} onClick={publishCurrentCase}><BadgeCheck size={15} />{currentDraftPublished ? `${selectedLatestVersion?.version} 已发布` : currentDraftDebugged ? "发布新版本" : "调试后发布"}</button></div>
        </div>
        <aside className="case-copilot"><div className="copilot-orb"><BrainCircuit size={21} /><i /></div><span>WORKFLOW REVIEW</span><h3>{!graphValidation.valid ? `编排图还有 ${graphValidation.issues.length} 个问题` : currentDraftDebugged ? "草稿已具备发布条件" : "当前 revision 需要重新调试"}</h3><p>{!graphValidation.valid ? `${graphValidation.issues[0]}。补齐类型端口和执行后继后，才能启动调试。` : currentDraftDebugged ? "依赖映射与确定性断言均已通过。发布会冻结节点、端口与策略，不再跟随草稿变化。" : "草稿发生了新改动，旧调试结果已自动失效。Task 仍继续使用已发布版本。"}</p><div className="review-checks"><span className={graphValidation.matchedPorts === graphValidation.totalPorts ? "" : "pending"}><CircleCheck size={13} />端口类型匹配 <b>{graphValidation.matchedPorts} / {graphValidation.totalPorts}</b></span><span className={graphValidation.valid ? "" : "pending"}><CircleCheck size={13} />数据依赖闭合 <b>{graphValidation.valid ? "CLOSED" : `${graphValidation.issues.length} ISSUES`}</b></span><span className={currentDraftDebugged && graphValidation.valid ? "" : "pending"}><Radio size={13} />当前调试结果 <b>{currentDraftDebugged && graphValidation.valid ? "PASSED" : "OUTDATED"}</b></span></div><div className="version-stack-card"><span>IMMUTABLE SNAPSHOT</span><strong>{selectedLatestVersion?.version ?? "—"}</strong><small>{selectedLatestVersion ? `来自 Draft r${selectedLatestVersion.sourceRevision} · ${selectedLatestVersion.publishedAt}` : "等待第一次发布"}</small></div><button onClick={() => { updateDraft("AI"); setWorkflowMode("ai"); }}>让 AI 增加负向分支 <Plus size={14} /></button></aside>
      </div>
    </section>
  );

  const launchView = (
    <section className="scene task-scene">
      <SceneIntro
        eyebrow={taskPanel === "center" ? "MISSION CONTROL · R26.07" : "TASK ASSEMBLY · NEW MISSION"}
        title={taskPanel === "center" ? "让每一次回归，都沿着自己的轨道运行。" : "把测试范围，展开成一张真实执行矩阵。"}
        description={taskPanel === "center" ? "任务记录一次真实批量执行；在这里观察进度、资源、失败聚集与下一次触发。" : "每一次选择都会立即反映为执行单元、账号需求、预计时间和 Agent 预算。"}
        action={taskPanel === "center"
          ? <button className="black-action" onClick={() => setTaskPanel("create")}><Plus size={15} />创建批量任务</button>
          : <button className="ghost-action" onClick={() => setTaskPanel("center")}><ArrowRight size={15} />返回任务中心</button>}
      />

      {taskPanel === "center" ? <>
        <div className="task-command-bar">
          <label><Search size={15} /><input aria-label="搜索任务" placeholder="搜索任务、迭代或触发来源" /></label>
          <div className="task-filter-pills"><button className="active">全部 18</button><button>运行中 2</button><button>需关注 2</button><button>等待资源 1</button></div>
          <div className="task-view-switch"><button className={taskViewMode === "orbit" ? "active" : ""} onClick={() => setTaskViewMode("orbit")}><Globe2 size={14} />轨道</button><button className={taskViewMode === "list" ? "active" : ""} onClick={() => setTaskViewMode("list")}><ListChecks size={14} />列表</button></div>
        </div>

        <div className="task-center-stage">
          <div className="task-orbit-board">
            <div className="task-board-heading"><div><span>ACTIVE TASK ORBIT</span><strong>{taskViewMode === "orbit" ? "任务运行轨道" : "任务运行磁带"}</strong></div><StatusPill tone="good"><Radio size={11} />调度器在线</StatusPill></div>
            {taskViewMode === "orbit" ? <div className="task-orbit-map">
              <div className="task-orbit-ring ring-one" /><div className="task-orbit-ring ring-two" /><div className="task-orbit-ring ring-three" />
              <button className="task-orbit-core" style={{ "--task-progress": `${taskProgress}%` } as React.CSSProperties} onClick={() => openTask(activeTask)}>
                <span>LIVE · {activeTask.id}</span><strong>{taskProgress}%</strong><small>{completedExecutions} / {activeTask.executions} EXECUTIONS</small><i />
              </button>
              {baseTaskRuns.slice(1, 4).map((task, index) => <button key={task.id} className={`task-satellite satellite-${index + 1} status-${task.status}`} onClick={() => openTask(task)}><span>{task.id}</span><strong>{task.name}</strong><small>{task.status === "attention" ? `${task.failed} 个新增失败` : task.status === "queued" ? "等待调度资源" : "质量门禁通过"}</small><i /></button>)}
              <div className="task-orbit-caption"><Bot size={15} /><span>8 个 Agent Worker 正在穿过执行矩阵</span></div>
            </div> : <div className="task-dense-list">
              {visibleTaskRuns.map((task) => <button key={task.id} onClick={() => openTask(task)}><i className={`task-state-dot status-${task.status}`} /><div><span>{task.id} · {task.trigger}</span><strong>{task.name}</strong></div><b>{task.id === activeTask.id ? taskProgress : task.progress}%</b><small>{task.passed} 通过 · {task.failed} 失败</small><em>{task.eta}</em><ArrowUpRight size={15} /></button>)}
            </div>}
          </div>

          <aside className="task-focus-lens">
            <div className="task-focus-top"><span>TASK FOCUS</span><StatusPill tone="violet">LIVE</StatusPill></div>
            <h2>{activeTask.name}</h2><p>{activeTask.matrix}</p>
            <div className="task-focus-progress"><div><strong>{completedExecutions}</strong><span>/ {activeTask.executions} 已完成</span></div><small>预计 8 分钟后结束</small><i><b style={{ width: `${taskProgress}%` }} /></i></div>
            <div className="task-focus-metrics"><div><span>稳定通过</span><strong>94.1%</strong></div><div><span>新增失败</span><strong className="risk">4</strong></div><div><span>环境异常</span><strong>2</strong></div><div><span>当前并发</span><strong>8 / 12</strong></div></div>
            <div className="task-snapshot"><span>冻结快照</span>{[`${activeManifestCount} CaseVersion · locked`, "角色身份 · version bound", "原子组件 11 · locked", "Atlas Agent · 2.1"].map((item) => <small key={item}><CircleCheck size={11} />{item}</small>)}</div>
            <button className="task-focus-action" onClick={() => openTask(activeTask)}>进入批量现场 <ArrowRight size={15} /></button>
          </aside>

          <div className="task-signal-strip">
            <div><Clock3 size={17} /><span>下一次计划</span><strong>23:00 · 预发全量夜间回归</strong><small>240 Executions</small></div>
            <div><Fingerprint size={17} /><span>身份容量</span><strong>21 / 29 可用</strong><small>可支持并发 12</small></div>
            <div><CircleAlert size={17} /><span>风险脉冲</span><strong>2 个失败簇待处理</strong><small>均来自 TASK-2047</small></div>
          </div>
        </div>

        <div className="task-history-rail">
          <div><span>最近任务</span><strong>{visibleTaskRuns.length}</strong></div>
          {visibleTaskRuns.slice(0, 4).map((task) => <button key={task.id} className={`history-task status-${task.status}`} onClick={() => openTask(task)}><span>{task.id}</span><strong>{task.name}</strong><small>{task.trigger}</small><b>{task.status === "running" ? `${task.id === activeTask.id ? taskProgress : task.progress}%` : task.status === "attention" ? "BLOCKED" : task.status === "queued" ? "QUEUED" : "PASSED"}</b><ArrowUpRight size={14} /></button>)}
        </div>
      </> : <>
        <div className="task-builder-steps">{["01 测试范围", "02 执行矩阵", "03 资源策略", "04 失败策略", "05 触发方式"].map((step, index) => <span className={index < 2 ? "active" : ""} key={step}><i>{index + 1}</i>{step.slice(3)}</span>)}</div>
        <div className="task-builder">
          <div className="builder-config">
            <section className="builder-panel scope-panel"><header><div><span>01 / VERSION VAULT</span><h3>选择已发布用例版本</h3></div><StatusPill tone="good">{selectedCaseCount} PINNED</StatusPill></header><p>Task 只保存不可变的 CaseVersion ID；草稿与调试结果不会进入正式批量执行。</p><div className="version-vault-grid">{publishedVersions.map((version) => <button key={version.id} className={taskVersionIds.includes(version.id) ? "selected" : ""} onClick={() => toggleTaskVersion(version.id)}><i>{taskVersionIds.includes(version.id) ? <Check size={12} /> : <Plus size={12} />}</i><div><span>{version.caseId} · {version.role}</span><strong>{version.caseName}</strong><small>来自 Draft r{version.sourceRevision} · {version.publishedAt}</small></div><b>{version.version}</b></button>)}</div><button className="impact-switch" onClick={() => { setTaskVersionIds(testCases.flatMap((item) => item.versions[0] ? [item.versions[0].id] : [])); setToast("AI 已选择每条受影响用例的最新已发布版本"); }}><Sparkles size={15} /><div><strong>AI 选择变更影响版本</strong><small>只会选择已发布快照，不会越过草稿边界</small></div><i /></button></section>

            <section className="builder-panel matrix-panel"><header><div><span>02 / MATRIX</span><h3>展开执行矩阵</h3></div><StatusPill tone="violet">实时计算</StatusPill></header><div className="matrix-option-group"><span>CaseVersion 内置角色 · 已锁定</span><div>{caseBoundRoles.map((role) => <button key={role} className="selected version-role-lock" onClick={() => setToast(`${role}身份已由 CaseVersion 冻结，不能在 Task 中覆盖`)}><Fingerprint size={13} />{role}<small>随版本执行</small><BadgeCheck size={12} /></button>)}</div></div><div className="matrix-option-group"><span>浏览器扩展维度</span><div>{["Chromium", "WebKit", "Firefox"].map((browserName) => <button key={browserName} className={taskBrowsers.includes(browserName) ? "selected" : ""} onClick={() => toggleOption(browserName, taskBrowsers, setTaskBrowsers)}><Globe2 size={13} />{browserName}</button>)}</div></div><div className="matrix-option-group"><span>执行环境</span><div><button className="selected"><Radio size={13} />预发环境</button><button>测试环境</button><button>本地隔离</button></div></div></section>

            <section className="builder-panel policy-panel"><header><div><span>03—04 / POLICY</span><h3>资源与失败策略</h3></div></header><div className="policy-row"><span>最大并发</span><div>{[4, 8, 12].map((value) => <button key={value} className={value === 8 ? "selected" : ""}>{value}</button>)}<button>自动</button></div></div><div className="policy-row"><span>环境失败重试</span><strong>2 次</strong></div><div className="policy-row"><span>Flaky 验证</span><strong>2 次</strong></div><div className="policy-row"><span>P0 / P1 立即停止</span><i className="policy-toggle on" /></div><div className="policy-note"><ShieldCheck size={15} /><p>产品失败不会自动重跑；环境失败会申请新的浏览器与身份租约。</p></div></section>

            <section className="builder-panel trigger-panel"><header><div><span>05 / TRIGGER</span><h3>选择触发方式</h3></div></header><div className="trigger-deck">{["立即执行", "每日 21:30", "CI Webhook", "发布完成后"].map((trigger) => <button key={trigger} className={taskTrigger === trigger ? "selected" : ""} onClick={() => setTaskTrigger(trigger)}>{trigger === "立即执行" ? <Play size={15} /> : trigger.includes("每日") ? <Clock3 size={15} /> : trigger.includes("CI") ? <Terminal size={15} /> : <Rocket size={15} />}<strong>{trigger}</strong><small>{trigger === "立即执行" ? "创建后进入现场" : trigger === "每日 21:30" ? "Asia / Tokyo" : trigger === "CI Webhook" ? "main → pre" : "部署成功时触发"}</small></button>)}</div></section>
          </div>

          <aside className="matrix-reactor">
            <span>MATRIX REACTOR</span>
            <div className="reactor-rings"><i /><i /><i /><i /><div><Component size={22} /><strong>{plannedExecutions}</strong><small>EXECUTIONS</small></div></div>
            <code>{selectedCaseCount} CaseVersion × {taskBrowsers.length} 浏览器</code>
            <div className="reactor-metrics"><div><span>预计耗时</span><strong>{Math.max(6, Math.ceil(plannedExecutions / 8 * 1.4))} 分钟</strong></div><div><span>峰值账号</span><strong>{caseBoundRoles.length}</strong></div><div><span>建议并发</span><strong>8</strong></div><div><span>Agent 预算</span><strong>{plannedExecutions * 12}</strong></div></div>
            <div className="reactor-ready"><CircleCheck size={15} /><div><strong>{selectedCaseCount} 个版本均已冻结</strong><small>21 个测试账号与 8 个 Worker 已就绪</small></div></div>
            <button onClick={taskTrigger === "立即执行" ? launchTask : createScheduledTask} disabled={plannedExecutions === 0}><Rocket size={16} />{taskTrigger === "立即执行" ? "创建并进入现场" : "创建调度任务"} <ArrowRight size={15} /></button>
            <button className="save-plan" onClick={() => setToast("已保存为测试计划模板")}>保存为测试计划</button>
          </aside>
        </div>
      </>}
    </section>
  );

  const batchLiveView = (
    <section className="scene live-scene batch-live-scene">
      <SceneIntro eyebrow={`TASK CONTROL · ${selectedTaskId}`} title={`${selectedTaskTotal} 条执行，正在同一片现场发生。`} description="从任务全局观察资源与失败，再进入任意 Execution 查看 Agent、浏览器和证据。" action={<StatusPill tone={running ? "violet" : taskProgress >= 100 ? "good" : "warn"}><Radio size={11} />{running ? "批量执行中" : taskProgress >= 100 ? "任务已完成" : "任务已暂停"}</StatusPill>} />

      <div className="run-context-banner run-kind-task"><div><Rocket size={18} /><span>TASK RUN</span><strong>{activeManifestCount} 个 CaseVersion Manifest</strong></div><p>Task 按创建时冻结的版本运行；后续草稿与新发布版本不会改变本次结果。</p><button onClick={() => navigate("results")}>查看正式结果 <ArrowRight size={13} /></button></div>

      <div className="task-live-ribbon">
        <div className="live-progress-core"><i style={{ "--task-progress": `${taskProgress}%` } as React.CSSProperties} /><div><span>OVERALL</span><strong>{taskProgress}%</strong></div></div>
        <div><span>完成</span><strong>{completedExecutions} / {selectedTaskTotal}</strong><small>预计 8 分钟结束</small></div>
        <div><span>稳定通过</span><strong>{executionCounts.passed}</strong><small>{completedExecutions ? Math.round(executionCounts.passed / completedExecutions * 1000) / 10 : 0}% stable</small></div>
        <div><span>产品失败</span><strong className="risk">{executionCounts.product}</strong><small>聚合为 1 个簇</small></div>
        <div><span>Flaky / 环境</span><strong>{executionCounts.flaky} / {executionCounts.infra}</strong><small>正在自动验证</small></div>
        <div><span>并发资源</span><strong>8 / 12</strong><small>账号 21 / 29</small></div>
      </div>

      <div className="batch-console">
        <aside className="worker-rail">
          <div className="batch-panel-title"><span>WORKER LANES</span><b>{String(workerLanes.length).padStart(2, "0")}</b></div>
          {workerLanes.map((lane, index) => <button key={lane} className={!activeTaskVersions.length && index === 2 ? "risk" : !activeTaskVersions.length && index === 4 ? "waiting" : "active"} onClick={() => setSelectedExecution(Math.min(selectedTaskTotal - 1, index * Math.max(1, selectedTaskBrowsers.length)))}><span>W{String(index + 1).padStart(2, "0")}</span><div><strong>{lane}</strong><small>{activeTaskVersions.length ? `${activeTaskVersions[index]?.version} · manifest locked` : index === 2 ? "断言失败" : index === 4 ? "等待身份" : `Agent ${5 + index}/12 actions`}</small></div><i /></button>)}
          <div className="worker-capacity"><span>Worker 容量</span><div><i style={{ width: "66%" }} /></div><b>8 / 12</b></div>
        </aside>

        <div className="execution-matrix-panel">
          <div className="matrix-panel-head"><div><span>EXECUTION MATRIX</span><strong>{selectedTaskMatrix}</strong></div><div><button className={matrixDimension === "role" ? "active" : ""} onClick={() => setMatrixDimension("role")}>按角色</button><button className={matrixDimension === "browser" ? "active" : ""} onClick={() => setMatrixDimension("browser")}>按浏览器</button></div></div>
          <div className="execution-matrix-groups" style={{ gridTemplateColumns: `repeat(${matrixGroupLabels.length}, minmax(135px, 1fr))` }}>
            {matrixGroupLabels.map((label) => {
              const groupExecutions = executionDescriptors.filter((execution) => matrixDimension === "role" ? execution.role === label : execution.browser === label);
              return <section className="execution-group" key={label}><header><span>{label}</span><b>{groupExecutions.length}</b></header><div>{groupExecutions.map((execution) => { const index = execution.index; const status = executionStates[index]; return <button key={index} data-execution={index} className={`execution-cell status-${status} ${selectedExecution === index ? "selected" : ""}`} onClick={() => setSelectedExecution(index)} onKeyDown={(event) => { const delta = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : event.key === "ArrowDown" ? 5 : event.key === "ArrowUp" ? -5 : 0; if (!delta) return; event.preventDefault(); const next = Math.max(0, Math.min(selectedTaskTotal - 1, index + delta)); setSelectedExecution(next); window.requestAnimationFrame(() => (document.querySelector(`[data-execution="${next}"]`) as HTMLElement | null)?.focus()); }} title={`Execution ${String(index + 1).padStart(3, "0")} · ${execution.caseId} · ${status}`} aria-label={`Execution ${String(index + 1).padStart(3, "0")} · ${execution.caseId} · ${status}`} tabIndex={selectedExecution === index ? 0 : -1}><i /></button>; })}</div></section>;
            })}
          </div>
          <div className="matrix-legend"><span><i className="running" />Running</span><span><i className="passed" />Passed</span><span><i className="product" />Product failed</span><span><i className="flaky" />Flaky</span><span><i className="infra" />Environment</span><span><i className="queued" />Queued</span></div>
          <div className="matrix-event-stream"><span>LATEST EVENTS</span><p><i />EXE-{String(selectedExecution + 1).padStart(3, "0")} 当前状态：{selectedExecutionState}</p><p><i className="risk" />{executionCounts.product} 个客户查询 502 已聚合为同一失败簇</p><small>21:48:05 · 持续更新</small></div>
        </div>

        <aside className="execution-inspector">
          <div className="inspector-top"><span>EXECUTION FOCUS</span><button aria-label="全屏查看当前执行"><Maximize2 size={14} /></button></div>
          <div className="execution-identity"><div>EXE-{String(selectedExecution + 1).padStart(3, "0")}</div><span>{selectedCaseLabel}{activeExecutionVersion ? ` ${activeExecutionVersion.version}` : ""} · {selectedRoleLabel} · {selectedBrowserLabel}</span><StatusPill tone={selectedExecutionState === "product" || selectedExecutionState === "infra" ? "warn" : selectedExecutionState === "passed" ? "good" : "violet"}>{selectedExecutionState.toUpperCase()}</StatusPill></div>
          {selectedExecutionState === "queued" ? <div className="execution-state-card waiting"><Clock3 size={22} /><span>WAITING FOR DISPATCH</span><strong>等待 Worker 与身份租约</strong><small>队列位置 {selectedExecution - completedExecutions + 1}</small></div> : selectedExecutionState === "infra" ? <div className="execution-state-card failed"><Globe2 size={22} /><span>ENVIRONMENT FAILED</span><strong>浏览器上下文创建超时</strong><small>将使用新身份租约自动重试</small></div> : <div className="execution-mini-browser"><header><i /><i /><i /><span>客户空间</span></header><main><div><Search size={13} /><span>TRK-{String(100 + selectedExecution)}</span><button>查询</button></div><p><strong>AI 测试客户 {selectedExecution + 1}</strong><code>{selectedExecutionState === "product" ? "502 ERROR" : `VIS-${2048 + selectedExecution}`}</code></p><em><MousePointer2 size={14} />{selectedExecutionState === "passed" ? "断言与证据已完成" : selectedExecutionState === "flaky" ? "正在进行 Flaky 复验" : selectedExecutionState === "product" ? "Agent 已捕获接口失败" : "Agent 正在验证结果行"}</em></main></div>}
          <div className="execution-path">{(activeExecutionWorkflow.length ? activeExecutionWorkflow.slice(0, 5).map((step) => step.name) : ["数据", "身份", "页面", "Agent", "断言"]).map((step, index, all) => <span className={index < all.length - 1 ? "done" : "current"} key={`${step}-${index}`}><i>{index < all.length - 1 ? <Check size={9} /> : index + 1}</i>{step}</span>)}</div>
          <div className="execution-evidence"><button><Camera size={14} /><span>截图</span><b>12</b></button><button><Network size={14} /><span>网络</span><b>36</b></button><button><Terminal size={14} /><span>日志</span><b>24</b></button></div>
          <div className="execution-agent-note"><BrainCircuit size={16} /><div><span>AGENT INTENT</span><strong>{selectedExecutionState === "queued" ? "等待执行上下文，不提前消耗 Agent 预算" : selectedExecutionState === "infra" ? "申请新的浏览器与身份租约后重试" : selectedExecutionState === "product" ? "保全接口、页面和数据证据并停止自动重试" : "确认筛选结果与预加载客户关系一致"}</strong></div></div>
        </aside>

        <div className="batch-controls"><button onClick={() => setToast(`已重新调度环境失败的 ${executionCounts.infra} 个 Execution`)}><RefreshCw size={15} />仅重跑环境失败</button><button className="control-main" onClick={() => setRunning((value) => !value)}>{running ? <Pause size={15} /> : <Play size={15} />}{running ? "暂停派发" : "继续派发"}</button><button onClick={() => setToast("已进入人工接管模式")}><Eye size={15} />接管当前执行</button><i /><span>{completedExecutions} / {selectedTaskTotal}</span><button onClick={() => navigate("results")}>查看阶段结果 <ArrowUpRight size={15} /></button></div>
      </div>
    </section>
  );

  const singleLiveView = (
    <section className="scene live-scene">
      <SceneIntro eyebrow="AGENT THEATRE · DEBUG RUN" title="这是一场草稿调试，不是一条正式结果。" description={`${debugContext?.caseId ?? selectedCase.id} · ${debugContext?.caseName ?? selectedCase.name} · Draft r${debugContext?.draftRevision ?? selectedCase.draftRevision}`} action={<StatusPill tone={runStep >= activeDebugSteps.length ? "good" : "violet"}><Radio size={11} />{runStep >= activeDebugSteps.length ? "调试通过" : running ? "Agent 执行中" : "已暂停"}</StatusPill>} />
      <div className="run-context-banner run-kind-debug"><div><TestTube2 size={18} /><span>DEBUG RUN</span><strong>Draft r{debugContext?.draftRevision ?? selectedCase.draftRevision} 快照</strong></div><p>结果只回写当前用例的调试状态，不进入正式 Task 结果和质量洞察。</p><button onClick={() => navigate("cases")}>返回用例 <ArrowRight size={13} /></button></div>
      <div className="theatre debug-theatre">
        <aside className="run-route">
          <div className="route-title"><span>DRAFT ROUTE</span><strong>{Math.min(runStep, activeDebugSteps.length)} / {activeDebugSteps.length}</strong></div>
          {activeDebugSteps.map((step, index) => <button key={step.id} className={`${index < runStep ? "done" : ""} ${index === runStep && running ? "current" : ""}`} onClick={() => index < runStep ? setRunStep(index) : setToast("尚未执行的节点不能提前跳过")}><span>{index < runStep ? <Check size={12} /> : index + 1}</span><div><strong>{step.name}</strong><small>{step.kind} · {step.phase}</small></div>{index < runStep && <i />}</button>)}
        </aside>
        <div className="browser-stage">
          <div className="stage-chrome"><div><i /><i /><i /></div><span><ShieldCheck size={12} /> test.crm.local/customers</span><Maximize2 size={14} /></div>
          <div className="crm-surface"><aside><div>C</div><span className="active"><UsersRound size={14} />客户</span><span><FileText size={14} />来访</span><span><Activity size={14} />分析</span></aside><main><div className="crm-heading"><div><span>CUSTOMERS</span><h3>客户空间</h3></div><button>+ 新建客户</button></div><div className="crm-search"><div><Search size={14} /><span>{runStep > debugFilterIndex ? "TRK-018" : "输入客户名称或追踪 ID"}</span></div><button className={runStep === debugFilterIndex || runStep === debugFilterIndex + 1 ? "agent-target" : ""}>查询</button></div><div className="crm-result"><span>客户</span><span>追踪 ID</span><span>最近来访</span>{runStep >= debugAssertIndex ? <><strong>AI 测试客户 018</strong><code>TRK-018</code><b>VIS-2048</b></> : <p>{runStep > debugFilterIndex ? "正在过滤结果…" : "等待 Agent 操作"}</p>}</div></main></div>
          {(runStep === debugFilterIndex || runStep === debugFilterIndex + 1) && <div className="agent-pointer"><MousePointer2 size={17} /><span>{runStep === debugFilterIndex ? "识别筛选入口" : "点击查询"}</span></div>}
        </div>
        <aside className="live-floats">
          <div className="intent-float"><div><BrainCircuit size={18} /><StatusPill tone="violet">AGENT</StatusPill></div><span>当前意图</span><h3>{runStep < debugFilterIndex ? "准备页面上下文" : runStep === debugFilterIndex ? "定位追踪 ID 筛选控件" : runStep < debugAssertIndex ? "输入变量并执行筛选" : "等待确定性断言完成"}</h3><footer><span>预算 {Math.max(0, 12 - runStep)} / 12</span><span>置信度 96%</span></footer></div>
          <div className="evidence-bubbles"><button><Camera size={15} /><span>截图</span><b>{Math.max(2, runStep * 2)}</b></button><button><Network size={15} /><span>网络</span><b>{Math.max(4, runStep * 6)}</b></button><button><Terminal size={15} /><span>日志</span><b>{Math.max(3, runStep * 4)}</b></button></div>
          <div className="event-float"><span>LATEST EVENT</span><p>{runStep >= activeDebugSteps.length ? "调试通过：当前 Draft 已具备发布条件" : runStep >= debugFilterIndex ? "已找到“客户名称或追踪 ID”输入框" : "数据与身份准备完成"}</p><small>21:48:05.{runStep}42</small></div>
        </aside>
        <div className="theatre-controls"><button aria-label="从头重跑" onClick={() => setRunStep(0)}><RefreshCw size={15} /></button><button className="control-main" onClick={() => { if (runStep >= activeDebugSteps.length) setRunStep(0); setRunning((value) => !value); }}>{running ? <Pause size={16} /> : <Play size={16} />}{running ? "暂停调试" : runStep >= activeDebugSteps.length ? "重新播放" : "继续调试"}</button><button onClick={() => setToast("已进入草稿人工接管模式")}><Eye size={15} />人工接管</button><i /><span>DEBUG</span><button onClick={() => navigate("cases")}><ArrowRight size={15} />{runStep >= activeDebugSteps.length ? "返回并发布" : "返回"}</button></div>
      </div>
    </section>
  );

  const liveView = liveMode === "single" ? singleLiveView : batchLiveView;

  const resultsView = (
    <section className="scene insights-scene task-result-scene">
      <SceneIntro eyebrow={`TASK RESULT · ${selectedTaskId}`} title={resultPassed ? "这次回归，可以进入下一道发布门。" : "结果不是一张报表，而是一次发布决定。"} description="区分产品问题、测试方法、环境失败与 Flaky，并把每个判断连接回真实证据。" action={<><button className="ghost-action" onClick={() => setToast("已导出任务证据包")}><FileText size={15} />导出证据</button><button className="black-action" onClick={() => setToast(resultPassed ? "已按相同冻结配置创建新任务" : `仅重跑 ${resultProductCount + resultMethodCount + resultEnvironmentCount} 个失败 Execution`)}><RefreshCw size={15} />{resultPassed ? "按此配置再运行" : "重跑失败单元"}</button></>} />

      <div className="result-gate-hero">
        <div className={`quality-gate-core ${resultPassed ? "passed" : ""}`}><span>QUALITY GATE</span><div className="gate-orbit"><i /><i /><strong>{resultGateLabel}</strong><small>稳定通过 {resultStableRate}%</small></div><p>{resultGateSummary}</p></div>
        <div className="result-score-grid"><div><span>执行单元</span><strong>{resultTaskTotal}</strong><small>{selectedTaskMatrix}</small></div><div><span>稳定通过</span><strong>{resultPassCount}</strong><small>{resultPassed ? "全部稳定通过" : "较上次 -2"}</small></div><div className={resultPassed ? "" : "risk"}><span>新增回归</span><strong>{resultProductCount}</strong><small>{resultPassed ? "无新增信号" : "集中于客户查询"}</small></div><div><span>测试 / 环境</span><strong>{resultMethodCount} / {resultEnvironmentCount}</strong><small>{resultPassed ? "无需处置" : "已从发布结论拆分"}</small></div></div>
        <div className={`result-ai-verdict ${resultPassed ? "passed" : ""}`}><div><BrainCircuit size={20} /><StatusPill tone={resultPassed ? "good" : "violet"}>AI VERDICT</StatusPill></div><span>发布建议</span><h3>{resultRecommendationTitle}</h3><p>{resultRecommendationDetail}</p><button onClick={() => resultPassed ? setToast("已展开冻结快照与门禁依据") : setSelectedCluster(visibleFailureClusters[0].id)}>{resultPassed ? "查看门禁依据" : "查看判断依据"} <ArrowRight size={14} /></button></div>
      </div>

      {resultPassed ? <div className="result-clear-state">
        <div className="clear-orbit"><i /><i /><ShieldCheck size={34} /><strong>100%</strong><span>STABLE</span></div>
        <div className="clear-copy"><span>NO NEW REGRESSIONS</span><h2>所有执行单元，都回到了稳定基线。</h2><p>数据初始化、角色权限、浏览器动作和确定性断言均已完成；3 个历史已知问题未计入本次门禁。</p><button onClick={() => setToast(`已展开 ${resultTaskTotal} 个 Execution 的完整证据目录`)}>查看完整证据目录 <ArrowRight size={14} /></button></div>
        <div className="clear-snapshot"><span>冻结快照</span>{[selectedTaskMatrix, `${activeManifestCount} CaseVersion · locked`, "11 个原子组件 · healthy", "Atlas Agent 2.1", "CRM R26.07 · rev 18"].map((item) => <small key={item}><CircleCheck size={12} />{item}</small>)}</div>
      </div> : <div className="result-workspace">
        <div className="failure-cluster-deck">
          <header><div><span>FAILURE CLUSTERS</span><strong>失败聚类</strong></div><button><Filter size={14} />全部类型</button></header>
          {visibleFailureClusters.map((cluster) => <button key={cluster.id} className={`failure-cluster-card cluster-${cluster.tone} ${selectedCluster === cluster.id ? "selected" : ""}`} onClick={() => setSelectedCluster(cluster.id)}><span>{cluster.kind}</span><strong>{cluster.name}</strong><small>{cluster.impact}</small><b>{cluster.count}</b><i style={{ "--cluster-confidence": `${cluster.confidence}%` } as React.CSSProperties} /><em>AI {cluster.confidence}%</em></button>)}
          <button className="known-issue-card"><CircleCheck size={16} /><div><strong>3 个已知问题已折叠</strong><small>不影响当前质量门禁计算</small></div><ChevronRight size={14} /></button>
        </div>

        <div className="failure-constellation">
          <div className="constellation-head"><div><span>IMPACT MAP</span><strong>{selectedClusterData.name}</strong></div><StatusPill tone="warn">{selectedClusterData.impact}</StatusPill></div>
          <div className="constellation-map"><div className="constellation-ring c-ring-one" /><div className="constellation-ring c-ring-two" /><div className="cluster-core"><CircleAlert size={23} /><strong>{selectedClusterData.count}</strong><span>FAILURES</span></div>{clusterNodes.map((node, index) => <button key={node} className={`impact-node impact-${index + 1}`}><i />{node}</button>)}</div>
          <div className="cluster-diagnosis"><span>ROOT CAUSE SIGNAL</span><code>{clusterSignal}</code><p>{clusterConclusion} <b>AI {selectedClusterData.confidence}%</b></p></div>
        </div>

        <aside className="result-triage">
          <header><span>TRIAGE ACTIONS</span><Settings2 size={15} /></header>
          <div className="triage-confidence"><span>{selectedClusterData.kind}置信度</span><strong>{selectedClusterData.confidence}%</strong><i><b style={{ width: `${selectedClusterData.confidence}%` }} /></i></div>
          <div className="triage-evidence"><span>共同证据</span>{clusterEvidence.map((item) => <small key={item}><Check size={11} />{item}</small>)}</div>
          <button className="triage-primary" onClick={() => setToast(`${clusterAction}已创建，并关联当前失败簇证据`)}>{clusterAction === "创建产品缺陷" ? <CircleAlert size={15} /> : clusterAction === "提交方法修订" ? <Sparkles size={15} /> : <Globe2 size={15} />}{clusterAction}</button>
          <button onClick={() => setToast("失败簇已标记为已知问题")}><BadgeCheck size={15} />标记已知问题</button>
          <button onClick={() => setToast("已提交测试方法候选修订") }><Sparkles size={15} />提交方法候选</button>
        </aside>
      </div>}

      <div className="result-baseline-rail">
        <div><span>与上次任务比较</span><strong>R26.07 · TASK-2045</strong></div><div><small>稳定通过</small><strong className={resultPassed ? "good" : "down"}>{resultPassed ? "+1.2%" : "-2.1%"}</strong></div><div><small>新增回归</small><strong className={resultPassed ? "good" : "risk"}>{resultPassed ? "0" : `+${resultProductCount}`}</strong></div><div><small>Flaky</small><strong className="good">{resultPassed ? "0" : "-1"}</strong></div><div><small>耗时</small><strong>{resultPassed ? "-00:41" : "+02:18"}</strong></div><button onClick={replaySelectedExecution}><Play size={14} />回放选中 Execution</button>
      </div>
    </section>
  );

  const insightsView = (
    <section className="scene insights-scene">
      <SceneIntro eyebrow="QUALITY TERRAIN · 30 DAYS" title="把失败放回它发生的旅程里。" description="洞察跨越多个 Task 观察质量趋势；发布判断仍然回到每一次冻结任务的真实结果。" action={<button className="black-action" onClick={() => openTask(latestRiskTask)}>查看最新风险任务 <ArrowUpRight size={15} /></button>} />
      <div className="terrain-stage">
        <div className="terrain-title"><span>R26.07 · QUALITY SIGNALS</span><strong>{insightExecutionCount}</strong><small>次 Execution · 30 天质量地形</small></div>
        <div className="quality-sphere">
          <div className="sphere-surface" /><div className="sphere-ring ring-a" /><div className="sphere-ring ring-b" /><div className="sphere-grid grid-a" /><div className="sphere-grid grid-b" />
          <svg viewBox="0 0 620 620" aria-hidden="true"><path d="M105 340 C178 184 303 160 370 246 S482 408 535 252" /><path d="M145 430 C258 330 338 370 476 180" /></svg>
          <button className="terrain-node terrain-node-one"><i /><span>{insightTerrain[0].label}</span><b>{insightTerrain[0].rate}</b></button><button className="terrain-node terrain-node-two risk"><i /><span>{insightTerrain[1].label}</span><b>{insightTerrain[1].rate}</b></button><button className="terrain-node terrain-node-three"><i /><span>{insightTerrain[2].label}</span><b>{insightTerrain[2].rate}</b></button><button className="terrain-node terrain-node-four"><i /><span>{insightTerrain[3].label}</span><b>{insightTerrain[3].rate}</b></button>
        </div>
        <aside className="terrain-metrics"><div><span>稳定通过</span><strong>{insightTrustedPassRate}</strong><small>{insightTrustedPassDelta}</small></div><div><span>方法健康度</span><strong>{insightMethodHealth}</strong><small>{insightMethodPopulation}</small></div><div className="risk-cluster"><CircleAlert size={20} /><span>ACTIVE RISK CLUSTER</span><h3>{insightRiskTitle}</h3><p>{insightRiskDetail}</p><button onClick={() => openTask(latestRiskTask)}>进入任务结果 <ArrowRight size={14} /></button></div></aside>
        <div className="replay-card"><div><span>TRACE REPLAY · FROZEN</span><h3>{replayVersion?.caseName ?? selectedCase.name} · {replayVersion?.version ?? "—"}</h3></div><div className="replay-path">{replayWorkflow.slice(0, 6).map((step) => <button className={step.atomId === "filter" || step.atomId === "asset-filter" ? "risk" : ""} key={step.id}><i /><span>{step.name}</span><small>{step.atomId === "filter" || step.atomId === "asset-filter" ? "signal" : "stable"}</small></button>)}</div><button className="replay-play" aria-label="回放正式 Execution" onClick={replaySelectedExecution}><Play size={16} /></button></div>
      </div>
    </section>
  );

  const sceneMap: Record<ViewId, React.ReactNode> = { space: spaceView, identities: identitiesView, atoms: atomsView, compose: composeView, cases: casesView, launch: launchView, live: liveView, results: resultsView, insights: insightsView };

  return (
    <div className={`site-world world-${view}`}>
      <div className="ambient-blob blob-one" /><div className="ambient-blob blob-two" />
      <div className="product-shell">
        <header className="floating-header">
          <button className="brand-lockup" onClick={() => navigate("space")}><span><Zap size={19} /></span><div><strong>atlas</strong><small>test space</small></div></button>
          <nav className={`nav-capsule ${mobileNav ? "open" : ""}`}>{views.map((item) => <button key={item.id} className={view === item.id ? "active" : ""} onClick={() => navigate(item.id)}>{item.label}</button>)}</nav>
          <div className="header-tools"><button className="project-chip" title={currentProjectName} onClick={() => setToast(`当前项目：${currentProjectName}`)}><span>{currentProjectMark}</span><div><small>当前空间</small><strong>{compactProjectName}</strong></div><ChevronDown size={13} /></button><button className="circle-tool" aria-label="搜索"><Search size={17} /></button><button className="circle-tool notification" aria-label="通知"><Bell size={17} /><i /></button><a className="user-orb" href="/login" aria-label="打开登录页面">{currentUserMark}</a><button className="mobile-nav" onClick={() => setMobileNav((value) => !value)}>{mobileNav ? <X size={18} /> : <Menu size={18} />}</button></div>
        </header>
        <main className="scene-host" key={view}>{sceneMap[view]}</main>
      </div>
      {toast && <div className="world-toast"><CircleCheck size={16} />{toast}</div>}
    </div>
  );
}
