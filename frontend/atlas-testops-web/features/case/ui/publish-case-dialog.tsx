"use client";

import { type FormEvent } from "react";

import { ApiProblemError } from "@/shared/api/problem";
import { createRequestId } from "@/shared/api/request-id";
import { Dialog } from "@/shared/ui/dialog/dialog";

import { usePublishCaseMutation } from "../api/case-queries";
import type { DebugRunViewModel } from "../model/case";

type PublishCaseDialogProps = {
  caseId: string;
  semanticRevision: number;
  debugRuns: DebugRunViewModel[];
  open: boolean;
  onClose: () => void;
};

export function PublishCaseDialog({
  caseId,
  semanticRevision,
  debugRuns,
  open,
  onClose
}: Readonly<PublishCaseDialogProps>) {
  const mutation = usePublishCaseMutation(caseId);
  const eligibleRuns = debugRuns.filter(
    (run) =>
      run.lifecycle === "TERMINATED" &&
      run.outcome === "PASSED" &&
      run.snapshotStatus === "CURRENT"
  );

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      await mutation.mutateAsync({
        clientMutationId: `publish-case-${createRequestId()}`,
        baseSemanticRevision: semanticRevision,
        debugRunId: String(form.get("debugRunId") ?? ""),
        version: String(form.get("version") ?? "").trim(),
        reviewSummary: String(form.get("reviewSummary") ?? "").trim()
      });
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
      title="发布 CaseVersion"
      description="发布会冻结当前语义 Revision、一个 PASSED DebugRun 与审核摘要。"
      onClose={onClose}
    >
      <form onSubmit={handleSubmit}>
        <label>
          版本号
          <input
            name="version"
            pattern="[0-9]+\.[0-9]+\.[0-9]+"
            placeholder="1.0.0"
            required
          />
        </label>
        <label>
          通过的 DebugRun
          <select
            name="debugRunId"
            defaultValue={eligibleRuns[0]?.id ?? ""}
            required
          >
            <option value="" disabled>
              {eligibleRuns.length
                ? "请选择 DebugRun"
                : "没有可发布的 PASSED DebugRun"}
            </option>
            {eligibleRuns.map((run) => (
              <option value={run.id} key={run.id}>
                {run.id.slice(0, 8)} · {run.completedAt?.toLocaleString("zh-CN")}
              </option>
            ))}
          </select>
        </label>
        <label>
          审核摘要
          <textarea
            name="reviewSummary"
            rows={4}
            maxLength={2000}
            placeholder="说明审核范围、证据和发布决定"
            required
          />
        </label>
        {errorMessage ? <p role="alert">{errorMessage}</p> : null}
        <footer>
          <button type="button" onClick={onClose}>
            取消
          </button>
          <button
            type="submit"
            disabled={mutation.isPending || !eligibleRuns.length}
          >
            {mutation.isPending ? "正在发布…" : "发布版本"}
          </button>
        </footer>
      </form>
    </Dialog>
  );
}
