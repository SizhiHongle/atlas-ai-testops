"use client";

import {
  ArrowRight,
  Fingerprint,
  KeyRound,
  Plus,
  ShieldCheck
} from "lucide-react";
import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import type { CSSProperties } from "react";

import { ErrorState } from "@/shared/ui/feedback/error-state";
import { EmptyState } from "@/shared/ui/feedback/empty-state";
import { LoadingState } from "@/shared/ui/feedback/loading-state";

import { useIdentityWalletQuery } from "../api/identity-queries";
import type { IdentityCardViewModel } from "../model/identity";
import styles from "./identity-page.module.css";

type RatioStyle = CSSProperties & { "--ratio": string };

function identityHref(
  pathname: string,
  searchParams: URLSearchParams,
  identityId: string
): string {
  const next = new URLSearchParams(searchParams);
  next.set("identity", identityId);
  return `${pathname}?${next.toString()}`;
}

function IdentityCard({
  identity,
  selected,
  href,
  index
}: Readonly<{
  identity: IdentityCardViewModel;
  selected: boolean;
  href: string;
  index: number;
}>) {
  return (
    <Link
      className={`${styles.identityCard} ${selected ? styles.selected : ""}`}
      href={href}
      aria-current={selected ? "true" : undefined}
    >
      <span>{identity.name}</span>
      <Fingerprint size={22} aria-hidden="true" />
      <strong>{identity.roleKey}</strong>
      <small>
        {identity.available} / {identity.total} 可用
      </small>
      <em>{identity.environmentLabel}</em>
      <b>{String(index + 1).padStart(2, "0")}</b>
    </Link>
  );
}

export function IdentityPage({ projectId }: Readonly<{ projectId: string }>) {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const wallet = useIdentityWalletQuery(projectId);

  if (wallet.isPending) {
    return <LoadingState label="正在读取身份钱包" />;
  }
  if (wallet.isError) {
    return (
      <ErrorState
        detail={wallet.error.message}
        onRetry={() => void wallet.refetch()}
      />
    );
  }

  const selectedId = searchParams.get("identity");
  const selected =
    wallet.data.identities.find((item) => item.id === selectedId) ??
    wallet.data.identities[0] ??
    null;
  const basePath = `/projects/${projectId}`;

  return (
    <div className={styles.page}>
      <header className={styles.hero}>
        <div>
          <p>
            <Fingerprint size={13} /> IDENTITY WALLET
          </p>
          <h1>把角色放进场景，而不是填进表格。</h1>
          <span>
            每张身份卡都携带权限、容量和健康状态；选择后即可带入真实用例上下文。
          </span>
        </div>
        <button
          type="button"
          disabled
          title="后端尚未提供原子化 Identity（Role + Pool + Account）创建接口"
        >
          <Plus size={16} /> 创建身份
        </button>
      </header>

      {!wallet.data.environment || !selected ? (
        <EmptyState
          title="还没有可用身份"
          detail="请先由项目管理员创建 ACTIVE Environment、TestRole、AccountPool 与 TestAccount。"
        />
      ) : (
        <>
          <section className={styles.stage}>
            <div className={styles.deck}>
              <header>
                <span>TEST IDENTITIES</span>
                <strong>
                  {String(wallet.data.identities.length).padStart(2, "0")}
                </strong>
              </header>
              <div className={styles.cards}>
                {wallet.data.identities.map((identity, index) => (
                  <IdentityCard
                    identity={identity}
                    selected={identity.id === selected.id}
                    href={identityHref(
                      pathname,
                      new URLSearchParams(searchParams.toString()),
                      identity.id
                    )}
                    index={index}
                    key={identity.id}
                  />
                ))}
              </div>
            </div>

            <aside className={styles.detail}>
              <header>
                <span>{selected.name.slice(0, 1)}</span>
                <div>
                  <small>SELECTED IDENTITY</small>
                  <h2>
                    {selected.name} · {selected.roleKey}
                  </h2>
                </div>
                <em data-status={selected.status}>{selected.status}</em>
              </header>

              <div
                className={styles.capacity}
                style={
                  {
                    "--ratio": `${Math.round(selected.readyRatio * 360)}deg`
                  } as RatioStyle
                }
              >
                <div>
                  <strong>{selected.available}</strong>
                  <span>可用账号</span>
                </div>
              </div>

              <div className={styles.capabilities}>
                <span>权限钥匙</span>
                <div>
                  {selected.capabilities.length ? (
                    selected.capabilities.map((capability) => (
                      <i key={capability}>
                        <KeyRound size={12} /> {capability}
                      </i>
                    ))
                  ) : (
                    <i>
                      <ShieldCheck size={12} /> 未声明 Capability
                    </i>
                  )}
                </div>
              </div>

              <div className={styles.metrics}>
                <span>
                  <i data-tone="success" /> 空闲 {selected.available}
                </span>
                <span>
                  <i data-tone="accent" /> 租用中 {selected.leased}
                </span>
                <span>
                  <i data-tone="danger" /> 隔离 {selected.quarantined}
                </span>
              </div>

              <Link
                className={styles.primary}
                href={`${basePath}/cases?roleId=${selected.roleId}`}
              >
                将身份放入场景 <ArrowRight size={16} />
              </Link>
            </aside>
          </section>

          <footer className={styles.leaseStrip}>
            <span>实时容量</span>
            <i>可用 {wallet.data.totals.available}</i>
            <i>租用中 {wallet.data.totals.leased}</i>
            <i>隔离 {wallet.data.totals.quarantined}</i>
            <button
              type="button"
              disabled
              title="公共 AccountLease 历史 API 尚未开放"
            >
              查看租约历史
            </button>
          </footer>
        </>
      )}
    </div>
  );
}
