"use client";

import { useRouter } from "next/navigation";
import { type FormEvent } from "react";

import type { IdentityCardViewModel } from "@/features/identity/model/identity";
import { ApiProblemError } from "@/shared/api/problem";
import { Dialog } from "@/shared/ui/dialog/dialog";

import { useCreateCaseMutation } from "../api/case-queries";
import {
  CASE_KEY_INPUT_PATTERN,
  finalizeCaseKeyInput,
  normalizeCaseKeyInput
} from "../model/case-key";

type CreateCaseDialogProps = {
  projectId: string;
  identities: IdentityCardViewModel[];
  preferredRoleId: string | null;
  open: boolean;
  onClose: () => void;
};

export function CreateCaseDialog({
  projectId,
  identities,
  preferredRoleId,
  open,
  onClose
}: Readonly<CreateCaseDialogProps>) {
  const router = useRouter();
  const mutation = useCreateCaseMutation(projectId);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const roleId = String(form.get("roleId") ?? "");
    const identity = identities.find((item) => item.roleId === roleId);
    const summary = String(form.get("summary") ?? "").trim();

    try {
      const caseId = await mutation.mutateAsync({
        caseKey: finalizeCaseKeyInput(
          String(form.get("caseKey") ?? "")
        ),
        name: String(form.get("name") ?? "").trim(),
        intentVersion: "0.1.0",
        intent: {
          schemaVersion: "atlas.test-intent/0.1",
          summary,
          actors: identity
            ? [
                {
                  actorSlot: "primary",
                  roleId: identity.roleId,
                  roleKey: identity.roleKey,
                  roleRevision: identity.roleRevision,
                  capabilities: [...identity.capabilities]
                }
              ]
            : [],
          requiredFeatures: [],
          requirementRefs: [],
          surfaces: [],
          evidencePolicy: {
            screenshots: "critical-actions",
            trace: true,
            retainSuccessDays: 7,
            retainFailureDays: 30
          },
          outcomePolicy: {
            agentMayDecidePass: false,
            evidenceIncompleteBlocksPass: true,
            requireHardOracle: true
          },
          recoveryPolicy: {
            maxUnitAttempts: 1,
            retryBrowserCrash: false,
            retryUnknownSideEffect: false
          }
        },
        graph: {
          schemaVersion: "atlas.workflow-graph/0.1",
          nodes: [],
          edges: []
        },
        layout: {}
      });
      router.replace(`?caseId=${caseId}`);
      onClose();
    } catch {
      // Mutation state renders the backend problem.
    }
  }

  const problem =
    mutation.error instanceof ApiProblemError
      ? mutation.error.problem
      : null;
  const firstViolation = problem?.violations?.[0];
  const errorMessage = firstViolation?.field === "body.caseKey"
    ? "稳定 Case Key 仅支持大写字母、数字和连字符，例如 CRM-CUSTOMER-FILTER。"
    : firstViolation
      ? `${problem.detail} ${firstViolation.message}`
      : problem?.detail ?? mutation.error?.message;

  return (
    <Dialog
      open={open}
      title="新建测试用例"
      description="TestCase 与唯一 WorkflowDraft 会由后端原子创建；初始空图会明确保持 INVALID。"
      onClose={onClose}
    >
      <form onSubmit={handleSubmit}>
        <label>
          用例名称
          <input
            name="name"
            minLength={1}
            maxLength={160}
            placeholder="销售筛选客户"
            required
          />
        </label>
        <label>
          稳定 Case Key
          <input
            name="caseKey"
            minLength={3}
            maxLength={80}
            pattern={CASE_KEY_INPUT_PATTERN}
            placeholder="CRM-CUSTOMER-FILTER"
            title="使用大写字母、数字和连字符，至少包含一个连字符。"
            onInput={(event) => {
              event.currentTarget.value = normalizeCaseKeyInput(
                event.currentTarget.value
              );
            }}
            required
          />
        </label>
        <label>
          测试意图
          <textarea
            name="summary"
            rows={4}
            maxLength={2000}
            placeholder="销售人员能够按跟进状态筛选本人客户"
            required
          />
        </label>
        <label>
          主身份
          <select name="roleId" defaultValue={preferredRoleId ?? ""}>
            <option value="">稍后绑定身份</option>
            {identities.map((identity) => (
              <option value={identity.roleId} key={identity.id}>
                {identity.name} · {identity.roleKey}
              </option>
            ))}
          </select>
        </label>
        {errorMessage ? <p role="alert">{errorMessage}</p> : null}
        <footer>
          <button type="button" onClick={onClose}>
            取消
          </button>
          <button type="submit" disabled={mutation.isPending}>
            {mutation.isPending ? "正在创建…" : "创建用例"}
          </button>
        </footer>
      </form>
    </Dialog>
  );
}
