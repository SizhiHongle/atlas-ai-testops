"use client";

import {
  usePathname,
  useRouter,
  useSearchParams
} from "next/navigation";
import {
  useState,
  type FormEvent
} from "react";

import { ApiProblemError } from "@/shared/api/problem";
import { createRequestId } from "@/shared/api/request-id";
import { canonicalDigest } from "@/shared/crypto/canonical-digest";
import { Dialog } from "@/shared/ui/dialog/dialog";

import { useStartTaskRunMutation } from "../api/task-queries";
import type { TaskPlanVersionViewModel } from "../model/task";

export function StartTaskRunDialog({
  projectId,
  version,
  open,
  onClose
}: Readonly<{
  projectId: string;
  version: TaskPlanVersionViewModel | null;
  open: boolean;
  onClose: () => void;
}>) {
  const pathname = usePathname();
  const router = useRouter();
  const searchParams = useSearchParams();
  const mutation = useStartTaskRunMutation(projectId);
  const [validationError, setValidationError] = useState<string | null>(null);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!version) return;
    const form = new FormData(event.currentTarget);
    const retryPolicyBody = {
      schemaVersion: "atlas.task-retry-policy/0.1" as const,
      infraRetryAttempts: Number(form.get("infraRetryAttempts")),
      maxTotalInfraRetries: Number(form.get("maxTotalInfraRetries")),
      initialBackoffSeconds: Number(form.get("initialBackoffSeconds")),
      maximumBackoffSeconds: Number(form.get("maximumBackoffSeconds")),
      jitterPercent: Number(form.get("jitterPercent"))
    };
    const contentDigest = await canonicalDigest(retryPolicyBody);

    if (contentDigest !== version.retryPolicyDigest) {
      setValidationError(
        `当前策略摘要 ${contentDigest} 与版本冻结摘要 ${version.retryPolicyDigest ?? "缺失"} 不一致。`
      );
      return;
    }

    setValidationError(null);
    const iterationId = String(form.get("iterationId") ?? "").trim();
    const clientMutationId = `start-run-${createRequestId()}`;

    try {
      const runId = await mutation.mutateAsync({
        taskPlanVersionId: version.id,
        command: {
          clientMutationId,
          iterationId: iterationId || null,
          retryPolicy: {
            ...retryPolicyBody,
            contentDigest
          }
        }
      });
      const next = new URLSearchParams(searchParams.toString());
      next.set("runId", runId);
      router.replace(`${pathname}?${next.toString()}`);
      onClose();
    } catch {
      // Mutation state renders the backend problem.
    }
  }

  const mutationError =
    mutation.error instanceof ApiProblemError
      ? mutation.error.problem.detail
      : mutation.error?.message;
  const errorMessage = validationError ?? mutationError;

  return (
    <Dialog
      open={open}
      title="启动真实 TaskRun"
      description={
        version
          ? `${version.versionRef} · ${version.matrixSize} 个预期矩阵单元`
          : "请选择一个已发布 TaskPlanVersion。"
      }
      onClose={onClose}
    >
      <form onSubmit={handleSubmit}>
        <label>
          迭代标识（可选）
          <input
            name="iterationId"
            minLength={3}
            maxLength={160}
            pattern="[A-Za-z0-9][A-Za-z0-9._:@/+=-]{2,159}"
            placeholder="release-2026.07"
          />
        </label>
        <label>
          单元基础设施重试次数
          <input
            name="infraRetryAttempts"
            type="number"
            min={0}
            max={4}
            defaultValue={1}
            required
          />
        </label>
        <label>
          整个任务最大基础设施重试数
          <input
            name="maxTotalInfraRetries"
            type="number"
            min={0}
            max={256}
            defaultValue={8}
            required
          />
        </label>
        <label>
          初始 / 最大退避秒数
          <span>
            <input
              name="initialBackoffSeconds"
              aria-label="初始退避秒数"
              type="number"
              min={1}
              max={300}
              defaultValue={2}
              required
            />
            <input
              name="maximumBackoffSeconds"
              aria-label="最大退避秒数"
              type="number"
              min={1}
              max={3600}
              defaultValue={30}
              required
            />
          </span>
        </label>
        <label>
          抖动百分比
          <input
            name="jitterPercent"
            type="number"
            min={0}
            max={50}
            defaultValue={10}
            required
          />
        </label>
        <p>
          版本冻结策略：<code>{version?.retryPolicyDigest ?? "未声明"}</code>
        </p>
        {errorMessage ? <p role="alert">{errorMessage}</p> : null}
        <footer>
          <button type="button" onClick={onClose}>
            取消
          </button>
          <button
            type="submit"
            disabled={!version?.retryPolicyDigest || mutation.isPending}
          >
            {mutation.isPending ? "正在启动…" : "创建并进入现场"}
          </button>
        </footer>
      </form>
    </Dialog>
  );
}
