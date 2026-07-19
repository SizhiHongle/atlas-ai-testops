"use client";

import {
  Activity,
  Atom,
  Bell,
  Boxes,
  BrainCircuit,
  ClipboardCheck,
  FlaskConical,
  LogOut,
  Menu,
  Search,
  Sparkles,
  UsersRound,
  X,
  Zap
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import type { ComponentType, ReactNode } from "react";

import { useLogoutMutation } from "@/features/auth/api/auth-queries";
import type { SessionViewModel } from "@/features/auth/model/session";
import { useNavigationStore } from "@/shared/state/navigation-store";

import styles from "./workspace-shell.module.css";

type NavigationItem = {
  label: string;
  segment: string;
  icon: ComponentType<{ size?: number }>;
};

const NAVIGATION: readonly NavigationItem[] = [
  { label: "测试空间", segment: "space", icon: Sparkles },
  { label: "身份", segment: "identities", icon: UsersRound },
  { label: "原子", segment: "fixtures/atoms", icon: Atom },
  { label: "资产", segment: "fixtures/assets", icon: Boxes },
  { label: "用例", segment: "cases", icon: FlaskConical },
  { label: "任务", segment: "tasks", icon: ClipboardCheck },
  { label: "现场", segment: "live", icon: Activity },
  { label: "结果", segment: "results", icon: BrainCircuit },
  { label: "洞察", segment: "insights", icon: Sparkles }
];

type WorkspaceShellProps = {
  session: SessionViewModel;
  children: ReactNode;
};

export function WorkspaceShell({
  session,
  children
}: Readonly<WorkspaceShellProps>) {
  const pathname = usePathname();
  const router = useRouter();
  const logout = useLogoutMutation();
  const navigationOpen = useNavigationStore(
    (state) => state.mobileNavigationOpen
  );
  const closeNavigation = useNavigationStore(
    (state) => state.closeMobileNavigation
  );
  const toggleNavigation = useNavigationStore(
    (state) => state.toggleMobileNavigation
  );
  const basePath = `/projects/${session.workspace.projectId}`;
  const darkCanvas = ["/space", "/tasks", "/live"].some((segment) =>
    pathname.endsWith(segment)
  );

  async function handleLogout() {
    try {
      await logout.mutateAsync();
    } finally {
      router.replace("/login");
    }
  }

  return (
    <div
      className={`${styles.world} ${
        darkCanvas ? styles.darkCanvas : styles.lightCanvas
      }`}
    >
      <header className={styles.header}>
        <Link className={styles.brand} href={`${basePath}/space`}>
          <span>
            <Zap size={17} aria-hidden="true" />
          </span>
          <div>
            <strong>atlas</strong>
            <small>test space</small>
          </div>
        </Link>

        <nav
          className={`${styles.navigation} ${navigationOpen ? styles.open : ""}`}
          aria-label="主导航"
        >
          {NAVIGATION.map((item) => {
            const href = `${basePath}/${item.segment}`;
            const active =
              pathname === href ||
              (item.segment === "cases" && pathname.startsWith(`${href}/`));
            const Icon = item.icon;
            return (
              <Link
                className={active ? styles.active : undefined}
                href={href}
                key={item.segment}
                onClick={closeNavigation}
              >
                <Icon size={15} />
                <span>{item.label}</span>
              </Link>
            );
          })}
        </nav>

        <div className={styles.actions}>
          <div className={styles.workspace}>
            <span>当前空间</span>
            <strong>{session.workspace.projectName}</strong>
          </div>
          <button type="button" aria-label="搜索" disabled title="全局搜索将在 Ontology 阶段开放">
            <Search size={17} />
          </button>
          <button type="button" aria-label="通知" disabled title="通知中心尚未接入">
            <Bell size={17} />
          </button>
          <button
            className={styles.avatar}
            type="button"
            onClick={() => void handleLogout()}
            disabled={logout.isPending}
            aria-label="退出登录"
            title={`${session.user.displayName} · 退出登录`}
          >
            {logout.isPending ? <LogOut size={16} /> : session.user.initials}
          </button>
          <button
            className={styles.menu}
            type="button"
            onClick={toggleNavigation}
            aria-label={navigationOpen ? "关闭导航" : "打开导航"}
            aria-expanded={navigationOpen}
          >
            {navigationOpen ? <X size={20} /> : <Menu size={20} />}
          </button>
        </div>
      </header>
      <main className={styles.content}>{children}</main>
    </div>
  );
}
