"use client";

import { AlertTriangle } from "lucide-react";

import styles from "./feedback.module.css";

type ErrorStateProps = {
  title?: string;
  detail: string;
  onRetry?: () => void;
};

export function ErrorState({
  title = "Atlas 服务暂时不可用",
  detail,
  onRetry
}: Readonly<ErrorStateProps>) {
  return (
    <section className={styles.state} role="alert">
      <span className={styles.icon}>
        <AlertTriangle aria-hidden="true" size={22} />
      </span>
      <p className={styles.eyebrow}>EXPLICIT FAILURE</p>
      <h1>{title}</h1>
      <p>{detail}</p>
      {onRetry ? (
        <div className={styles.actions}>
          <button className={styles.primary} type="button" onClick={onRetry}>
            重新连接
          </button>
        </div>
      ) : null}
    </section>
  );
}
