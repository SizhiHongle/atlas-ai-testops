import { LoaderCircle } from "lucide-react";

import styles from "./feedback.module.css";

export function LoadingState({
  label = "正在连接 Atlas 测试空间"
}: Readonly<{ label?: string }>) {
  return (
    <section className={styles.state} aria-live="polite">
      <span className={styles.icon}>
        <LoaderCircle aria-hidden="true" size={22} />
      </span>
      <p className={styles.eyebrow}>ATLAS CONNECTING</p>
      <h1>{label}</h1>
      <p>正在读取真实服务状态，不会回退到演示数据。</p>
    </section>
  );
}
