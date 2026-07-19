"use client";

import type { FormEvent } from "react";

import { ApiProblemError } from "@/shared/api/problem";
import { createRequestId } from "@/shared/api/request-id";
import { Dialog } from "@/shared/ui/dialog/dialog";

import { useReviseClassificationMutation } from "../api/result-queries";
import type {
  FailureClusterViewModel,
  RequestFailureClassificationRevisionCommand
} from "../model/result";

const FAILURE_DOMAINS = [
  "PRODUCT",
  "TEST_SPEC",
  "TEST_DATA",
  "IDENTITY",
  "ENVIRONMENT",
  "INFRASTRUCTURE",
  "EXTERNAL_DEPENDENCY",
  "AGENT_AUTOMATION",
  "POLICY_SECURITY",
  "EVIDENCE",
  "CLEANUP",
  "UNKNOWN"
] as const;

export function ReviewClassificationDialog({
  snapshotId,
  cluster,
  open,
  onClose
}: Readonly<{
  snapshotId: string | null;
  cluster: FailureClusterViewModel | null;
  open: boolean;
  onClose: () => void;
}>) {
  const mutation = useReviseClassificationMutation(snapshotId);
  const classification = cluster?.classification ?? null;

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!classification) return;
    const form = new FormData(event.currentTarget);

    try {
      await mutation.mutateAsync({
        classificationId: classification.id,
        command: {
          expectedRevision: classification.revision,
          failureDomain: String(
            form.get("failureDomain")
          ) as RequestFailureClassificationRevisionCommand["failureDomain"],
          hypothesisCode: String(form.get("hypothesisCode")).trim(),
          hypothesis: String(form.get("hypothesis")).trim(),
          confidence: {
            numerator: Math.round(Number(form.get("confidence")) * 100),
            denominator: 10000
          },
          supportingEvidenceRefs: classification.supportingEvidenceRefs,
          contradictingEvidenceRefs:
            classification.contradictingEvidenceRefs,
          evidenceGapCodes: classification.evidenceGapCodes,
          judgmentState: String(
            form.get("judgmentState")
          ) as RequestFailureClassificationRevisionCommand["judgmentState"],
          clientMutationId: `review-classification-${createRequestId()}`
        }
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
      title="复核失败归因"
      description="基于 exact Classification revision 追加一条不可变人工判断；原始 AI 版本不会被覆盖。"
      onClose={onClose}
    >
      <form onSubmit={handleSubmit}>
        <label>
          Failure Domain
          <select
            name="failureDomain"
            defaultValue={classification?.domain ?? "UNKNOWN"}
            required
          >
            {FAILURE_DOMAINS.map((domain) => (
              <option value={domain} key={domain}>
                {domain}
              </option>
            ))}
          </select>
        </label>
        <label>
          Hypothesis Code
          <input
            name="hypothesisCode"
            defaultValue={classification?.hypothesisCode ?? ""}
            pattern="[A-Z][A-Z0-9_]{1,95}"
            minLength={2}
            maxLength={96}
            required
          />
        </label>
        <label>
          判断说明
          <textarea
            name="hypothesis"
            defaultValue={classification?.hypothesis ?? ""}
            minLength={1}
            maxLength={500}
            rows={4}
            required
          />
        </label>
        <label>
          置信度（0—100）
          <input
            name="confidence"
            type="number"
            min={0}
            max={100}
            step={0.1}
            defaultValue={classification?.confidence ?? 0}
            required
          />
        </label>
        <label>
          人工判断
          <select name="judgmentState" defaultValue="HUMAN_REVISED" required>
            <option value="HUMAN_CONFIRMED">HUMAN_CONFIRMED</option>
            <option value="HUMAN_REVISED">HUMAN_REVISED</option>
            <option value="HUMAN_REJECTED">HUMAN_REJECTED</option>
          </select>
        </label>
        <p>
          将保留 {classification?.supportingEvidenceRefs.length ?? 0} 条支持证据和{" "}
          {classification?.contradictingEvidenceRefs.length ?? 0} 条反证引用。
        </p>
        {errorMessage ? <p role="alert">{errorMessage}</p> : null}
        <footer>
          <button type="button" onClick={onClose}>
            取消
          </button>
          <button
            type="submit"
            disabled={!classification || mutation.isPending}
          >
            {mutation.isPending ? "正在提交…" : "追加人工 Revision"}
          </button>
        </footer>
      </form>
    </Dialog>
  );
}
