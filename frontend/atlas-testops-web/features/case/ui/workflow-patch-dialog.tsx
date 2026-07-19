"use client";

import { type FormEvent, useState } from "react";

import { ApiProblemError } from "@/shared/api/problem";
import { Dialog } from "@/shared/ui/dialog/dialog";

import {
  useApplyWorkflowPatchMutation,
  usePreviewWorkflowPatchMutation
} from "../api/case-queries";
import type {
  WorkflowEdgeViewModel,
  WorkflowNodeViewModel,
  WorkflowPatchCommand
} from "../model/case";
import {
  createHumanWorkflowPatch,
  decodePortReference,
  encodePortReference,
  parsePortDefinitions,
  type WorkflowPatchOperation
} from "../model/workflow-patch-builder";
import styles from "./case-page.module.css";

type OperationKind =
  | "ADD_NODE"
  | "REMOVE_NODE"
  | "ADD_EDGE"
  | "REMOVE_EDGE";

type WorkflowPatchDialogProps = {
  caseId: string;
  semanticRevision: number;
  nodes: WorkflowNodeViewModel[];
  edges: WorkflowEdgeViewModel[];
  open: boolean;
  onClose: () => void;
};

function mutationMessage(error: unknown): string | null {
  if (error instanceof ApiProblemError) return error.problem.detail;
  return error instanceof Error ? error.message : null;
}

function requiredString(form: FormData, name: string, label: string): string {
  const value = String(form.get(name) ?? "").trim();
  if (!value) throw new Error(`${label}不能为空。`);
  return value;
}

function buildOperation(
  kind: OperationKind,
  form: FormData
): WorkflowPatchOperation {
  if (kind === "ADD_NODE") {
    const phase = requiredString(form, "phase", "Phase") as
      | "setup"
      | "identity"
      | "execute"
      | "assert"
      | "cleanup";
    return {
      op: "ADD_NODE",
      node: {
        id: requiredString(form, "nodeId", "Node ID"),
        kind: requiredString(form, "nodeKind", "Node Kind"),
        versionRef: requiredString(form, "versionRef", "Version Ref"),
        phase,
        terminal: form.get("terminal") === "on",
        inputPorts: parsePortDefinitions(
          String(form.get("inputPorts") ?? "")
        ),
        outputPorts: parsePortDefinitions(
          String(form.get("outputPorts") ?? "")
        ),
        params: {}
      }
    };
  }
  if (kind === "REMOVE_NODE") {
    return {
      op: "REMOVE_NODE",
      nodeId: requiredString(form, "nodeId", "Node")
    };
  }
  if (kind === "REMOVE_EDGE") {
    return {
      op: "REMOVE_EDGE",
      edgeId: requiredString(form, "edgeId", "Edge")
    };
  }

  const source = decodePortReference(
    requiredString(form, "sourcePort", "Source Port")
  );
  const target = decodePortReference(
    requiredString(form, "targetPort", "Target Port")
  );
  if (
    source.semanticType !== target.semanticType ||
    source.kind !== target.kind
  ) {
    throw new Error("Source 与 Target Port 的类型和 Kind 必须完全一致。");
  }
  return {
    op: "ADD_EDGE",
    edge: {
      id: requiredString(form, "edgeId", "Edge ID"),
      sourceNodeId: source.nodeId,
      sourcePort: source.key,
      targetNodeId: target.nodeId,
      targetPort: target.key,
      semanticType: source.semanticType,
      kind: source.kind as "data" | "control",
      mapping: "direct"
    }
  };
}

export function WorkflowPatchDialog({
  caseId,
  semanticRevision,
  nodes,
  edges,
  open,
  onClose
}: Readonly<WorkflowPatchDialogProps>) {
  const [operationKind, setOperationKind] =
    useState<OperationKind>("ADD_NODE");
  const [candidate, setCandidate] = useState<WorkflowPatchCommand | null>(
    null
  );
  const [clientError, setClientError] = useState<string | null>(null);
  const preview = usePreviewWorkflowPatchMutation(caseId);
  const apply = useApplyWorkflowPatchMutation(caseId);

  function resetCandidate() {
    setCandidate(null);
    setClientError(null);
    preview.reset();
    apply.reset();
  }

  function close() {
    resetCandidate();
    onClose();
  }

  async function handlePreview(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      const patch = createHumanWorkflowPatch(
        semanticRevision,
        buildOperation(operationKind, form),
        String(form.get("rationaleSummary") ?? "")
      );
      setClientError(null);
      setCandidate(patch);
      await preview.mutateAsync(patch);
    } catch (error) {
      setCandidate(null);
      setClientError(
        error instanceof Error ? error.message : "无法创建 WorkflowPatch。"
      );
    }
  }

  async function handleApply() {
    if (!candidate || !preview.data?.applicable) return;
    try {
      await apply.mutateAsync(candidate);
      close();
    } catch {
      // Mutation state renders the backend problem.
    }
  }

  const sourcePorts = nodes.flatMap((node) =>
    node.outputPorts.map((port) => ({
      value: encodePortReference(node.id, port),
      label: `${node.id} · ${port.key} · ${port.semanticType}`
    }))
  );
  const targetPorts = nodes.flatMap((node) =>
    node.inputPorts.map((port) => ({
      value: encodePortReference(node.id, port),
      label: `${node.id} · ${port.key} · ${port.semanticType}`
    }))
  );
  const errorMessage =
    clientError ??
    mutationMessage(preview.error) ??
    mutationMessage(apply.error);

  return (
    <Dialog
      open={open}
      title="人工编排 WorkflowPatch"
      description="先调用后端预检，再用同一个 Patch ID 原子应用；Revision 冲突不会被前端覆盖。"
      onClose={close}
    >
      <form
        onSubmit={handlePreview}
        onChange={() => {
          if (candidate || preview.data || errorMessage) resetCandidate();
        }}
      >
        <label>
          语义操作
          <select
            value={operationKind}
            onChange={(event) =>
              setOperationKind(event.target.value as OperationKind)
            }
          >
            <option value="ADD_NODE">添加 Node</option>
            <option value="REMOVE_NODE">删除 Node</option>
            <option value="ADD_EDGE">连接 Typed Edge</option>
            <option value="REMOVE_EDGE">删除 Edge</option>
          </select>
        </label>

        {operationKind === "ADD_NODE" ? (
          <>
            <label>
              Node ID
              <input
                name="nodeId"
                pattern="[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127}"
                placeholder="crm.customer.create"
                required
              />
            </label>
            <label>
              Node Kind
              <input name="nodeKind" placeholder="browser.action" required />
            </label>
            <label>
              Published Version Ref
              <input
                name="versionRef"
                placeholder="browser.action@1.2.0"
                required
              />
            </label>
            <label>
              Phase
              <select name="phase" defaultValue="execute">
                <option value="setup">setup</option>
                <option value="identity">identity</option>
                <option value="execute">execute</option>
                <option value="assert">assert</option>
                <option value="cleanup">cleanup</option>
              </select>
            </label>
            <label>
              Input Ports
              <textarea
                name="inputPorts"
                rows={3}
                placeholder="customer_id | crm.customer.id | data | required"
              />
            </label>
            <label>
              Output Ports
              <textarea
                name="outputPorts"
                rows={3}
                placeholder="created_id | crm.customer.id | data | required"
              />
            </label>
            <label className={styles.patchCheckbox}>
              <input name="terminal" type="checkbox" />
              Terminal Node
            </label>
          </>
        ) : null}

        {operationKind === "REMOVE_NODE" ? (
          <label>
            Node
            <select name="nodeId" defaultValue="" required>
              <option value="" disabled>
                选择要删除的 Node
              </option>
              {nodes.map((node) => (
                <option value={node.id} key={node.id}>
                  {node.id} · {node.kind}
                </option>
              ))}
            </select>
          </label>
        ) : null}

        {operationKind === "ADD_EDGE" ? (
          <>
            <label>
              Edge ID
              <input
                name="edgeId"
                pattern="[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127}"
                placeholder="customer-created"
                required
              />
            </label>
            <label>
              Source Output
              <select name="sourcePort" defaultValue="" required>
                <option value="" disabled>
                  {sourcePorts.length
                    ? "选择 Source Port"
                    : "当前 Graph 没有 Output Port"}
                </option>
                {sourcePorts.map((port) => (
                  <option value={port.value} key={port.value}>
                    {port.label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Target Input
              <select name="targetPort" defaultValue="" required>
                <option value="" disabled>
                  {targetPorts.length
                    ? "选择 Target Port"
                    : "当前 Graph 没有 Input Port"}
                </option>
                {targetPorts.map((port) => (
                  <option value={port.value} key={port.value}>
                    {port.label}
                  </option>
                ))}
              </select>
            </label>
          </>
        ) : null}

        {operationKind === "REMOVE_EDGE" ? (
          <label>
            Edge
            <select name="edgeId" defaultValue="" required>
              <option value="" disabled>
                选择要删除的 Edge
              </option>
              {edges.map((edge) => (
                <option value={edge.id} key={edge.id}>
                  {edge.id} · {edge.sourceNodeId} → {edge.targetNodeId}
                </option>
              ))}
            </select>
          </label>
        ) : null}

        <label>
          变更说明
          <textarea
            name="rationaleSummary"
            rows={3}
            maxLength={1000}
            placeholder="说明本次人工语义变更的原因"
          />
        </label>

        {preview.data ? (
          <section
            className={styles.patchPreview}
            data-applicable={preview.data.applicable}
          >
            <span>
              {preview.data.applicable ? "PATCH APPLICABLE" : "PATCH REJECTED"}
            </span>
            <strong>
              {preview.data.nodeCount} Nodes · {preview.data.edgeCount} Edges
            </strong>
            <small>
              Graph {preview.data.graphValid ? "VALID" : "INVALID"} ·{" "}
              {preview.data.semanticDigest.slice(0, 20)}…
            </small>
            {preview.data.issues.slice(0, 3).map((issue, index) => (
              <p key={`${issue.code}-${index}`}>
                {issue.code} · {issue.message}
              </p>
            ))}
          </section>
        ) : null}

        {errorMessage ? <p role="alert">{errorMessage}</p> : null}
        <footer>
          <button type="button" onClick={close}>
            取消
          </button>
          <button type="submit" disabled={preview.isPending || apply.isPending}>
            {preview.isPending ? "正在预检…" : "预检 Patch"}
          </button>
          <button
            className={styles.patchApply}
            type="button"
            onClick={() => void handleApply()}
            disabled={
              !candidate ||
              !preview.data?.applicable ||
              preview.isPending ||
              apply.isPending
            }
          >
            {apply.isPending ? "正在应用…" : "原子应用"}
          </button>
        </footer>
      </form>
    </Dialog>
  );
}
