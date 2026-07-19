"use client";

import {
  ArrowRight,
  BadgeCheck,
  Bot,
  ChevronDown,
  Eye,
  EyeOff,
  Fingerprint,
  KeyRound,
  LockKeyhole,
  Mail,
  Network,
  Radio,
  ShieldCheck,
  Sparkles,
  TestTube2,
  Zap
} from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { ApiProblemError } from "@/shared/api/problem";
import { LOGIN_WORKSPACES } from "@/shared/config/client";

import { useLoginMutation } from "../api/auth-queries";
import styles from "./login-page.module.css";

export function LoginPage() {
  const router = useRouter();
  const login = useLoginMutation();
  const [passwordVisible, setPasswordVisible] = useState(false);
  const [workspaceId, setWorkspaceId] = useState(LOGIN_WORKSPACES[0].id);
  const [configurationError, setConfigurationError] = useState<string | null>(
    null
  );

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (login.isPending) return;

    const form = new FormData(event.currentTarget);
    const selectedWorkspace = LOGIN_WORKSPACES.find(
      (workspace) => workspace.id === workspaceId
    );
    const email = String(form.get("email") ?? "").trim();
    const password = String(form.get("password") ?? "");
    const remember = form.get("remember") === "on";

    if (!selectedWorkspace?.configured) {
      setConfigurationError(
        "当前部署尚未配置 Tenant 与 Project，请联系平台管理员。"
      );
      return;
    }

    setConfigurationError(null);
    try {
      const session = await login.mutateAsync({
        tenantId: selectedWorkspace.tenantId,
        projectId: selectedWorkspace.projectId,
        email,
        password,
        remember
      });
      router.replace(`/projects/${session.workspace.projectId}/space`);
    } catch {
      // The mutation state renders the backend error in the form.
    }
  }

  const errorDetail =
    configurationError ??
    (login.error instanceof ApiProblemError
      ? login.error.problem.detail
      : login.error?.message);

  return (
    <main className={styles.world}>
      <section className={styles.story} aria-label="Atlas 测试空间介绍">
        <header className={styles.storyHeader}>
          <Link className={styles.brand} href="/" aria-label="返回 Atlas">
            <span>
              <Zap size={19} aria-hidden="true" />
            </span>
            <div>
              <strong>atlas</strong>
              <small>test space</small>
            </div>
          </Link>
          <span className={styles.gatewayState}>
            <i />
            安全登录入口
          </span>
        </header>

        <div className={styles.storyCopy}>
          <p className={styles.kicker}>
            <Sparkles size={13} aria-hidden="true" />
            IDENTITY GATE · R26.07
          </p>
          <h1>
            一次登录，
            <br />
            唤醒整座测试空间。
          </h1>
          <p>
            连接你的项目身份、测试账号与 Agent 权限。每一次进入，都从可信上下文开始。
          </p>
        </div>

        <div className={styles.identityField} aria-hidden="true">
          <i className={styles.orbitOne} />
          <i className={styles.orbitTwo} />
          <div className={styles.identityCore}>
            <span>ATLAS ID</span>
            <div>
              <Fingerprint size={30} />
            </div>
            <strong>CH</strong>
            <small>OWNER · VERIFIED</small>
          </div>
          <span className={styles.signalOne}>
            <KeyRound size={14} />
            <i>
              账号池 <b>登录后读取</b>
            </i>
          </span>
          <span className={styles.signalTwo}>
            <Bot size={14} />
            <i>
              Agent <b>登录后校验</b>
            </i>
          </span>
          <span className={styles.signalThree}>
            <ShieldCheck size={14} />
            <i>
              证据链 <b>Session 隔离</b>
            </i>
          </span>
          <span className={styles.signalFour}>
            <TestTube2 size={14} />
            <i>
              当前空间 <b>{LOGIN_WORKSPACES[0].label.split(" · ")[0]}</b>
            </i>
          </span>
        </div>

        <footer className={styles.storyFooter}>
          <span>
            <Radio size={14} /> 工作空间上下文
          </span>
          <div>
            <span>
              <b>角色</b> 登录后读取
            </span>
            <span>
              <b>账号</b> 登录后读取
            </span>
            <span>
              <b>服务</b> 连接后验证
            </span>
          </div>
        </footer>
      </section>

      <section className={styles.access} aria-label="登录表单">
        <header className={styles.accessHeader}>
          <span>
            <Network size={14} /> SAME-ORIGIN · BFF
          </span>
          <button type="button" disabled title="帮助中心尚未接入">
            需要帮助？
          </button>
        </header>
        <div className={styles.card}>
          <p className={styles.cardBadge}>
            <BadgeCheck size={14} /> 安全访问
          </p>
          <h2>回到你的测试空间</h2>
          <p className={styles.intro}>
            身份、数据、Agent 与证据链正在等待连接。
          </p>

          <form onSubmit={handleSubmit}>
            <label>
              <span>测试空间</span>
              <span className={styles.selectField}>
                <TestTube2 size={17} />
                <select
                  name="workspace"
                  value={workspaceId}
                  onChange={(event) => {
                    setWorkspaceId(event.target.value as "primary" | "staging");
                    setConfigurationError(null);
                  }}
                  aria-label="测试空间"
                >
                  {LOGIN_WORKSPACES.map((workspace) => (
                    <option value={workspace.id} key={workspace.id}>
                      {workspace.configured
                        ? workspace.label
                        : `${workspace.label} · 尚未配置`}
                    </option>
                  ))}
                </select>
                <ChevronDown size={15} />
              </span>
            </label>
            <label>
              <span>邮箱或工号</span>
              <span className={styles.inputField}>
                <Mail size={17} />
                <input
                  name="email"
                  type="email"
                  placeholder="name@company.com"
                  autoComplete="username"
                  required
                />
              </span>
            </label>
            <label>
              <span>密码</span>
              <span className={`${styles.inputField} ${styles.passwordField}`}>
                <LockKeyhole size={17} />
                <input
                  name="password"
                  type={passwordVisible ? "text" : "password"}
                  autoComplete="current-password"
                  placeholder="输入你的访问密码"
                  required
                />
                <button
                  type="button"
                  onClick={() => setPasswordVisible((value) => !value)}
                  aria-label={passwordVisible ? "隐藏密码" : "显示密码"}
                >
                  {passwordVisible ? <EyeOff size={17} /> : <Eye size={17} />}
                </button>
              </span>
            </label>

            <div className={styles.formOptions}>
              <label className={styles.remember}>
                <input name="remember" type="checkbox" defaultChecked />
                <span>保持登录状态</span>
              </label>
              <button type="button" disabled title="密码找回尚未接入">
                忘记密码
              </button>
            </div>

            {errorDetail ? (
              <p className={styles.error} role="alert">
                {errorDetail}
              </p>
            ) : null}

            <button
              className={styles.submit}
              type="submit"
              disabled={login.isPending}
            >
              {login.isPending ? "正在验证身份…" : "登录测试空间"}
              <ArrowRight size={17} aria-hidden="true" />
            </button>
          </form>

          <div className={styles.divider}>
            <span>或使用可信身份</span>
          </div>

          <button
            className={styles.disabledProvider}
            type="button"
            disabled
            title="Feishu OAuth 尚未由后端开放"
          >
            <span className={styles.feishuMark} aria-hidden="true" />
            飞书一键登录
            <ArrowRight size={17} />
          </button>
          <p className={styles.consent}>
            继续即表示你同意《访问协议》和《隐私策略》，所有登录行为将写入安全审计。
          </p>
        </div>
        <footer className={styles.accessFooter}>
          <ShieldCheck size={14} />
          端到端加密 <i /> Atlas Identity Protocol 2.4
        </footer>
      </section>
    </main>
  );
}
