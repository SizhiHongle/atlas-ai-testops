"use client";

import { RefreshCw } from "lucide-react";

import { useReadiness } from "../../lib/api/health";
import { ApiProblemError } from "../../lib/api/problem";
import styles from "./status.module.css";

export function StatusCard() {
  const { data, error, isLoading, isValidating, mutate } = useReadiness();
  const problem = error instanceof ApiProblemError ? error.problem : undefined;

  return (
    <div className={styles.statusCard} aria-live="polite">
      <div>
        <span
          className={`${styles.indicator} ${
            data?.status === "ready" ? styles.ready : styles.waiting
          }`}
        />
        <strong>
          {isLoading
            ? "正在连接"
            : data?.status === "ready"
              ? "控制面可用"
              : "控制面不可用"}
        </strong>
      </div>

      {data ? (
        <dl className={styles.details}>
          <div>
            <dt>服务</dt>
            <dd>{data.service}</dd>
          </div>
          <div>
            <dt>版本</dt>
            <dd>{data.version}</dd>
          </div>
          <div>
            <dt>环境</dt>
            <dd>{data.environment}</dd>
          </div>
          {(data.checks ?? []).map((check) => (
            <div key={check.name}>
              <dt>{check.name}</dt>
              <dd>{check.status}</dd>
            </div>
          ))}
        </dl>
      ) : null}

      {error ? (
        <p className={styles.error}>
          {problem
            ? `${problem.detail}（Request ID: ${problem.requestId}）`
            : "无法连接 Atlas API，请检查 NEXT_PUBLIC_ATLAS_API_URL。"}
        </p>
      ) : null}

      <button
        className={styles.refresh}
        type="button"
        disabled={isValidating}
        onClick={() => void mutate()}
      >
        <RefreshCw size={15} className={isValidating ? styles.spinning : undefined} />
        重新检查
      </button>
    </div>
  );
}
