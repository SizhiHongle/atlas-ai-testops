import { Inbox } from "lucide-react";
import type { ReactNode } from "react";

import styles from "./feedback.module.css";

type EmptyStateProps = {
  title: string;
  detail: string;
  action?: ReactNode;
};

export function EmptyState({
  title,
  detail,
  action
}: Readonly<EmptyStateProps>) {
  return (
    <section className={styles.empty}>
      <span className={styles.icon}>
        <Inbox aria-hidden="true" size={21} />
      </span>
      <div>
        <h2>{title}</h2>
        <p>{detail}</p>
      </div>
      {action}
    </section>
  );
}
