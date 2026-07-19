"use client";

import {
  ArrowRight,
  Boxes,
  Box,
  Plus
} from "lucide-react";
import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { useState } from "react";

import { useSessionQuery } from "@/features/auth/api/auth-queries";
import { EmptyState } from "@/shared/ui/feedback/empty-state";
import { ErrorState } from "@/shared/ui/feedback/error-state";
import { LoadingState } from "@/shared/ui/feedback/loading-state";

import { useFixtureCatalogQuery } from "../api/fixture-queries";
import { CreateAssetDialog } from "./create-asset-dialog";
import styles from "./fixture-page.module.css";

const ASSET_MANAGERS = new Set([
  "ORG_ADMIN",
  "PROJECT_ADMIN",
  "COMPONENT_MAINTAINER"
]);

export function AssetsPage({ projectId }: Readonly<{ projectId: string }>) {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const session = useSessionQuery();
  const catalog = useFixtureCatalogQuery(projectId);
  const [createOpen, setCreateOpen] = useState(false);
  const canCreate =
    session.data?.roles.some((role) => ASSET_MANAGERS.has(role)) ?? false;

  if (catalog.isPending) return <LoadingState label="正在读取资产目录" />;
  if (catalog.isError) {
    return (
      <ErrorState
        detail={catalog.error.message}
        onRetry={() => void catalog.refetch()}
      />
    );
  }

  const selectedId = searchParams.get("blueprintId");
  const selected =
    catalog.data.blueprints.find((item) => item.id === selectedId) ??
    catalog.data.blueprints[0] ??
    null;
  const basePath = `/projects/${projectId}`;

  function blueprintHref(blueprintId: string): string {
    const next = new URLSearchParams(searchParams.toString());
    next.set("blueprintId", blueprintId);
    return `${pathname}?${next.toString()}`;
  }

  return (
    <div className={styles.page}>
      <header className={styles.hero}>
        <div>
          <p>
            <Boxes size={13} /> ORCHESTRATION ASSET LIBRARY
          </p>
          <h1>成熟路线，应该成为可复用的资产。</h1>
          <span>
            资产封装可复用片段；Node 数、Export 数与版本状态均由 Blueprint Catalog 返回。
          </span>
        </div>
        <Link href={`${basePath}/cases`}>
          回到用例工作室 <ArrowRight size={16} />
        </Link>
      </header>

      <div className={styles.targetBar}>
        <Box size={17} />
        <div>
          <span>当前目标用例</span>
          <strong>尚未选择 · 在用例工作室中绑定</strong>
        </div>
        <Link href={`${basePath}/cases`}>
          选择目标 <ArrowRight size={14} />
        </Link>
      </div>

      {!selected ? (
        <EmptyState
          title="还没有 DataBlueprint"
          detail="创建稳定 Blueprint 身份后，再为它编写版本契约并通过发布门禁。"
          action={
            <button
              type="button"
              disabled={!canCreate}
              onClick={() => setCreateOpen(true)}
            >
              创建资产
            </button>
          }
        />
      ) : (
        <section className={styles.workspace}>
          <div className={styles.catalog}>
            <div className={`${styles.grid} ${styles.blueprintGrid}`}>
              {catalog.data.blueprints.map((blueprint) => (
                <article
                  className={`${styles.card} ${blueprint.id === selected.id ? styles.selected : ""}`}
                  key={blueprint.id}
                >
                  <Link
                    className={styles.cardBody}
                    href={blueprintHref(blueprint.id)}
                  >
                    <span>{blueprint.key}</span>
                    <Boxes size={20} />
                    <strong>{blueprint.name}</strong>
                    <small>
                      {blueprint.version} · {blueprint.nodeCount} Nodes
                    </small>
                    <em>{blueprint.versionState}</em>
                  </Link>
                  <Link
                    className={styles.cardAction}
                    href={`${basePath}/cases?blueprintId=${blueprint.id}`}
                  >
                    加入当前用例 <Plus size={13} />
                  </Link>
                </article>
              ))}
            </div>
          </div>

          <aside className={styles.detail}>
            <header>
              <span>
                <Boxes size={25} />
              </span>
              <em>{selected.versionState}</em>
            </header>
            <p>SELECTED ASSET</p>
            <h2>{selected.name}</h2>
            <span className={styles.description}>{selected.description}</span>
            <div className={styles.meta}>
              <div>
                <span>稳定 Key</span>
                <strong>{selected.key}</strong>
              </div>
              <div>
                <span>节点数量</span>
                <strong>{selected.nodeCount}</strong>
              </div>
              <div>
                <span>输出数量</span>
                <strong>{selected.exportCount}</strong>
              </div>
              <div>
                <span>Plan Digest</span>
                <strong>
                  {selected.planDigest
                    ? selected.planDigest.slice(0, 12)
                    : "尚未生成"}
                </strong>
              </div>
            </div>
            <Link
              className={styles.primary}
              href={`${basePath}/cases?blueprintId=${selected.id}`}
            >
              选择用例并套用 <ArrowRight size={16} />
            </Link>
          </aside>
        </section>
      )}

      <button
        type="button"
        disabled={!canCreate}
        title={canCreate ? "创建稳定 DataBlueprint 身份" : "需要资产维护权限"}
        onClick={() => setCreateOpen(true)}
        style={{ marginTop: 14 }}
      >
        <Plus size={15} /> 创建编排资产
      </button>

      <CreateAssetDialog
        projectId={projectId}
        kind="blueprint"
        open={createOpen}
        onClose={() => setCreateOpen(false)}
      />
    </div>
  );
}
