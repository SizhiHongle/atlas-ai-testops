import { ShieldX } from "lucide-react";

import styles from "./feedback.module.css";

export function AccessDenied() {
  return (
    <section className={styles.state} role="alert">
      <span className={styles.icon}>
        <ShieldX aria-hidden="true" size={22} />
      </span>
      <p className={styles.eyebrow}>PERMISSION GUARD</p>
      <h1>当前身份无权访问此能力</h1>
      <p>权限由 Atlas Session 实时返回，请联系项目管理员调整角色。</p>
    </section>
  );
}
