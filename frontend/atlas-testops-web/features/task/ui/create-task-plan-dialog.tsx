"use client";

import {
  usePathname,
  useRouter,
  useSearchParams
} from "next/navigation";
import type { FormEvent } from "react";

import { ApiProblemError } from "@/shared/api/problem";
import { createRequestId } from "@/shared/api/request-id";
import { Dialog } from "@/shared/ui/dialog/dialog";

import { useCreateTaskPlanMutation } from "../api/task-queries";

export function CreateTaskPlanDialog({
  projectId,
  open,
  onClose
}: Readonly<{
  projectId: string;
  open: boolean;
  onClose: () => void;
}>) {
  const pathname = usePathname();
  const router = useRouter();
  const searchParams = useSearchParams();
  const mutation = useCreateTaskPlanMutation(projectId);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);

    try {
      const taskPlanId = await mutation.mutateAsync({
        taskKey: String(form.get("taskKey") ?? "").trim(),
        name: String(form.get("name") ?? "").trim(),
        clientMutationId: `create-plan-${createRequestId()}`
      });
      const next = new URLSearchParams(searchParams.toString());
      next.set("planId", taskPlanId);
      next.delete("runId");
      router.replace(`${pathname}?${next.toString()}`);
      onClose();
    } catch {
      // Mutation state renders the backend problem.
    }
  }

  const errorMessage =
    mutation.error instanceof ApiProblemError
      ? mutation.error.problem.detail
      : mutation.error?.message;

  return (
    <Dialog
      open={open}
      title="创建 TaskPlan"
      description="创建稳定、可版本化的批量任务身份；正式运行只会消费后续发布的不可变版本。"
      onClose={onClose}
    >
      <form onSubmit={handleSubmit}>
        <label>
          任务名称
          <input
            name="name"
            minLength={1}
            maxLength={160}
            placeholder="客户核心回归"
            required
          />
        </label>
        <label>
          稳定 Task Key
          <input
            name="taskKey"
            minLength={3}
            maxLength={160}
            pattern="[a-z][a-z0-9]*(?:[._-][a-z0-9]+){0,7}"
            placeholder="crm.customer.regression"
            required
          />
        </label>
        {errorMessage ? <p role="alert">{errorMessage}</p> : null}
        <footer>
          <button type="button" onClick={onClose}>
            取消
          </button>
          <button type="submit" disabled={mutation.isPending}>
            {mutation.isPending ? "正在创建…" : "创建 TaskPlan"}
          </button>
        </footer>
      </form>
    </Dialog>
  );
}
