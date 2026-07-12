"use client";

import {
  ArrowRight,
  BadgeCheck,
  Bot,
  ChevronDown,
  CircleCheck,
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
import { useState, type FormEvent } from "react";

type LoginState = "idle" | "account" | "feishu";

export default function LoginPage() {
  const [passwordVisible, setPasswordVisible] = useState(false);
  const [remembered, setRemembered] = useState(true);
  const [loginState, setLoginState] = useState<LoginState>("idle");

  const enterSpace = (method: Exclude<LoginState, "idle">) => {
    if (loginState !== "idle") return;
    setLoginState(method);
    window.setTimeout(() => window.location.assign("/"), 950);
  };

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    enterSpace("account");
  };

  return (
    <div className="login-world">
      <div className="login-glow login-glow-one" />
      <div className="login-glow login-glow-two" />

      <main className="login-shell">
        <section className="login-story" aria-label="Atlas 测试空间介绍">
          <header className="login-brand-row">
            <a className="login-brand" href="/" aria-label="返回 Atlas 测试空间">
              <span><Zap size={19} /></span>
              <div><strong>atlas</strong><small>test space</small></div>
            </a>
            <div className="login-system-state"><i />身份网关在线</div>
          </header>

          <div className="login-story-copy">
            <div className="login-kicker"><Sparkles size={13} />IDENTITY GATE · R26.07</div>
            <h1>一次登录，<br />唤醒整座测试空间。</h1>
            <p>连接你的项目身份、测试账号与 Agent 权限。每一次进入，都从可信上下文开始。</p>
          </div>

          <div className="login-identity-field" aria-hidden="true">
            <div className="login-orbit login-orbit-a" />
            <div className="login-orbit login-orbit-b" />
            <div className="login-orbit login-orbit-c" />
            <div className="login-core">
              <span>ATLAS ID</span>
              <div><Fingerprint size={35} /></div>
              <strong>CH</strong>
              <small>OWNER · VERIFIED</small>
            </div>
            <div className="login-signal signal-account"><KeyRound size={15} /><span>账号池</span><b>23 可用</b></div>
            <div className="login-signal signal-agent"><Bot size={15} /><span>Agent</span><b>权限已签名</b></div>
            <div className="login-signal signal-project"><TestTube2 size={15} /><span>当前空间</span><b>客户运营</b></div>
            <div className="login-signal signal-evidence"><ShieldCheck size={15} /><span>证据链</span><b>安全连接</b></div>
          </div>

          <footer className="login-story-footer">
            <div><Radio size={14} /><span>实时身份网络</span></div>
            <div className="login-mini-metrics"><span><b>4</b>角色</span><span><b>23</b>账号</span><span><b>99.9%</b>可用</span></div>
          </footer>
        </section>

        <section className="login-access" aria-label="登录表单">
          <div className="login-access-top">
            <div><Network size={15} /><span>CN · 企业节点</span></div>
            <button type="button">需要帮助？</button>
          </div>

          <div className="login-card">
            <div className="login-card-heading">
              <span><BadgeCheck size={15} />安全访问</span>
              <h2>回到你的测试空间</h2>
              <p>身份、数据、Agent 与证据链正在等待连接。</p>
            </div>

            <form onSubmit={handleSubmit}>
              <label className="login-field">
                <span>测试空间</span>
                <div className="login-select-wrap">
                  <TestTube2 size={17} />
                  <select defaultValue="crm" aria-label="选择测试空间">
                    <option value="crm">客户运营 · CRM R26.07</option>
                    <option value="staging">预发验证 · STAGING</option>
                  </select>
                  <ChevronDown size={15} />
                </div>
              </label>

              <label className="login-field">
                <span>邮箱或工号</span>
                <div className="login-input-wrap">
                  <Mail size={17} />
                  <input type="text" name="account" placeholder="name@company.com" autoComplete="username" required />
                </div>
              </label>

              <label className="login-field">
                <span>密码</span>
                <div className="login-input-wrap">
                  <LockKeyhole size={17} />
                  <input type={passwordVisible ? "text" : "password"} name="password" placeholder="输入你的访问密码" autoComplete="current-password" required />
                  <button type="button" onClick={() => setPasswordVisible((value) => !value)} aria-label={passwordVisible ? "隐藏密码" : "显示密码"}>
                    {passwordVisible ? <EyeOff size={16} /> : <Eye size={16} />}
                  </button>
                </div>
              </label>

              <div className="login-form-options">
                <button className={`login-check ${remembered ? "checked" : ""}`} type="button" onClick={() => setRemembered((value) => !value)} aria-pressed={remembered}>
                  <i>{remembered && <CircleCheck size={15} />}</i>保持登录状态
                </button>
                <button type="button">忘记密码</button>
              </div>

              <button className="login-primary" type="submit" disabled={loginState !== "idle"}>
                <span>{loginState === "account" ? "正在验证身份…" : "登录测试空间"}</span>
                {loginState === "account" ? <i className="login-spinner" /> : <ArrowRight size={17} />}
              </button>
            </form>

            <div className="login-divider"><span>或使用可信身份</span></div>

            <button className="feishu-login" type="button" onClick={() => enterSpace("feishu")} disabled={loginState !== "idle"}>
              <span className="feishu-mark" aria-hidden="true"><i /><i /><i /><i /></span>
              <strong>{loginState === "feishu" ? "正在连接飞书身份…" : "飞书一键登录"}</strong>
              {loginState === "feishu" ? <i className="login-spinner dark" /> : <ArrowRight size={17} />}
            </button>

            <p className="login-consent">继续即表示你同意《访问协议》和《隐私策略》，所有登录行为将写入安全审计。</p>
          </div>

          <div className="login-access-foot"><ShieldCheck size={14} /><span>端到端加密</span><i />Atlas Identity Protocol 2.4</div>
        </section>
      </main>

      <div className={`login-progress ${loginState !== "idle" ? "visible" : ""}`} role="status" aria-live="polite">
        <span><BadgeCheck size={16} /></span>
        <div><strong>{loginState === "feishu" ? "正在连接飞书" : "正在验证 Atlas 身份"}</strong><small>即将进入客户运营测试空间</small></div>
        <i className="login-spinner dark" />
      </div>
    </div>
  );
}
