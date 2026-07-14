import type { Metadata } from "next";

import { StatusCard } from "./status-card";
import styles from "./status.module.css";

export const metadata: Metadata = {
  title: "系统状态 | Atlas AI 测试平台"
};

export default function SystemStatusPage() {
  return (
    <main className={styles.page}>
      <section className={styles.panel}>
        <p className={styles.eyebrow}>ATLAS CONTROL PLANE</p>
        <h1>系统连接状态</h1>
        <p className={styles.intro}>
          本页使用后端 OpenAPI 生成的 TypeScript 类型，并通过共享 SWR Client 读取真实
          readiness 接口。
        </p>
        <StatusCard />
      </section>
    </main>
  );
}
