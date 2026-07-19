import { createRequestId } from "@/shared/api/request-id";

import type {
  WorkflowPatchCommand,
  WorkflowPortViewModel
} from "./case";

export type WorkflowPatchOperation =
  WorkflowPatchCommand["operations"][number];

const PORT_KEY_PATTERN = /^[A-Za-z_][A-Za-z0-9_.-]{0,127}$/;

export function parsePortDefinitions(
  value: string
): WorkflowPortViewModel[] {
  return value
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line, index) => {
      const [key, semanticType, kind = "data", requirement = "required"] =
        line.split("|").map((part) => part.trim());
      if (!key || !PORT_KEY_PATTERN.test(key)) {
        throw new Error(`第 ${index + 1} 行 Port Key 不合法。`);
      }
      if (!semanticType || semanticType.length > 128) {
        throw new Error(`第 ${index + 1} 行 Semantic Type 不合法。`);
      }
      if (!["data", "control"].includes(kind)) {
        throw new Error(`第 ${index + 1} 行 Port Kind 必须是 data 或 control。`);
      }
      if (!["required", "optional"].includes(requirement)) {
        throw new Error(
          `第 ${index + 1} 行 Required 标记必须是 required 或 optional。`
        );
      }
      return {
        key,
        semanticType,
        kind: kind as "data" | "control",
        required: requirement === "required",
        sensitive: false
      };
    });
}

export function encodePortReference(
  nodeId: string,
  port: WorkflowPortViewModel
): string {
  return JSON.stringify({
    nodeId,
    key: port.key,
    semanticType: port.semanticType,
    kind: port.kind
  });
}

export function decodePortReference(value: string): {
  nodeId: string;
  key: string;
  semanticType: string;
  kind: string;
} {
  const parsed = JSON.parse(value) as Record<string, unknown>;
  if (
    typeof parsed.nodeId !== "string" ||
    typeof parsed.key !== "string" ||
    typeof parsed.semanticType !== "string" ||
    !["data", "control"].includes(String(parsed.kind))
  ) {
    throw new Error("端口引用不合法。");
  }
  return {
    nodeId: parsed.nodeId,
    key: parsed.key,
    semanticType: parsed.semanticType,
    kind: String(parsed.kind)
  };
}

export function createHumanWorkflowPatch(
  baseSemanticRevision: number,
  operation: WorkflowPatchOperation,
  rationaleSummary: string
): WorkflowPatchCommand {
  return {
    patchId: globalThis.crypto.randomUUID(),
    clientMutationId: `workflow-patch-${createRequestId()}`,
    baseSemanticRevision,
    source: "human",
    operations: [operation],
    rationaleSummary: rationaleSummary.trim() || null
  };
}
