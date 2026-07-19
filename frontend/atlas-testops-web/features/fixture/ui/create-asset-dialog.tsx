"use client";

import { useRouter } from "next/navigation";
import { type FormEvent } from "react";

import { ApiProblemError } from "@/shared/api/problem";
import { Dialog } from "@/shared/ui/dialog/dialog";

import {
  useCreateAtomMutation,
  useCreateBlueprintMutation
} from "../api/fixture-queries";

type CreateAssetDialogProps = {
  projectId: string;
  kind: "atom" | "blueprint";
  open: boolean;
  onClose: () => void;
};

export function CreateAssetDialog({
  projectId,
  kind,
  open,
  onClose
}: Readonly<CreateAssetDialogProps>) {
  const router = useRouter();
  const atomMutation = useCreateAtomMutation(projectId);
  const blueprintMutation = useCreateBlueprintMutation(projectId);
  const mutation = kind === "atom" ? atomMutation : blueprintMutation;

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const name = String(form.get("name") ?? "").trim();
    const key = String(form.get("key") ?? "").trim();
    const description = String(form.get("description") ?? "").trim();

    try {
      if (kind === "atom") {
        const result = await atomMutation.mutateAsync({
          name,
          atomKey: key,
          businessDomain: String(form.get("businessDomain") ?? "").trim(),
          description
        });
        router.replace(`?atomId=${result.id}`);
      } else {
        const result = await blueprintMutation.mutateAsync({
          name,
          blueprintKey: key,
          description
        });
        router.replace(`?blueprintId=${result.id}`);
      }
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
      title={kind === "atom" ? "制造原子" : "创建编排资产"}
      description="先创建稳定资产身份；版本契约与发布证据将在资产详情中独立维护。"
      onClose={onClose}
    >
      <form onSubmit={handleSubmit}>
        <label>
          名称
          <input
            name="name"
            minLength={1}
            maxLength={160}
            placeholder={kind === "atom" ? "创建客户" : "客户数据初始化"}
            required
          />
        </label>
        <label>
          稳定 Key
          <input
            name="key"
            minLength={1}
            maxLength={160}
            pattern="[A-Za-z0-9][A-Za-z0-9._-]*"
            placeholder={
              kind === "atom" ? "customer.create" : "customer.bootstrap"
            }
            required
          />
        </label>
        {kind === "atom" ? (
          <label>
            业务域
            <input
              name="businessDomain"
              minLength={1}
              maxLength={160}
              placeholder="customer"
              required
            />
          </label>
        ) : null}
        <label>
          描述
          <textarea
            name="description"
            rows={4}
            maxLength={2000}
            placeholder="说明业务能力、边界和预期用途"
            required
          />
        </label>
        {errorMessage ? <p role="alert">{errorMessage}</p> : null}
        <footer>
          <button type="button" onClick={onClose}>
            取消
          </button>
          <button type="submit" disabled={mutation.isPending}>
            {mutation.isPending ? "正在创建…" : "确认创建"}
          </button>
        </footer>
      </form>
    </Dialog>
  );
}
