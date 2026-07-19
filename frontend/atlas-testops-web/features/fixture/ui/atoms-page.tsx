"use client";

import {
  ArrowRight,
  Atom,
  Boxes,
  Plus,
  Search,
  Sparkles
} from "lucide-react";
import Link from "next/link";
import {
  usePathname,
  useRouter,
  useSearchParams
} from "next/navigation";
import { useState, type FormEvent } from "react";

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

export function AtomsPage({ projectId }: Readonly<{ projectId: string }>) {
  const pathname = usePathname();
  const router = useRouter();
  const searchParams = useSearchParams();
  const session = useSessionQuery();
  const catalog = useFixtureCatalogQuery(projectId);
  const [createOpen, setCreateOpen] = useState(false);
  const canCreate =
    session.data?.roles.some((role) => ASSET_MANAGERS.has(role)) ?? false;

  if (catalog.isPending) return <LoadingState label="正在读取原子目录" />;
  if (catalog.isError) {
    return (
      <ErrorState
        detail={catalog.error.message}
        onRetry={() => void catalog.refetch()}
      />
    );
  }

  const query = (searchParams.get("q") ?? "").trim().toLocaleLowerCase();
  const atoms = catalog.data.atoms.filter(
    (atom) =>
      !query ||
      [atom.name, atom.key, atom.domain, atom.description].some((value) =>
        value.toLocaleLowerCase().includes(query)
      )
  );
  const selectedId = searchParams.get("atomId");
  const selected =
    atoms.find((atom) => atom.id === selectedId) ??
    catalog.data.atoms.find((atom) => atom.id === selectedId) ??
    atoms[0] ??
    null;
  const basePath = `/projects/${projectId}`;

  function submitSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const next = new URLSearchParams(searchParams.toString());
    const value = String(form.get("q") ?? "").trim();
    if (value) next.set("q", value);
    else next.delete("q");
    next.delete("atomId");
    router.replace(`${pathname}?${next.toString()}`);
  }

  function atomHref(atomId: string): string {
    const next = new URLSearchParams(searchParams.toString());
    next.set("atomId", atomId);
    return `${pathname}?${next.toString()}`;
  }

  return (
    <div className={styles.page}>
      <header className={styles.hero}>
        <div>
          <p>
            <Atom size={13} /> ATOMIC LAB
          </p>
          <h1>业务能力，像积木一样有形。</h1>
          <span>
            输入和输出决定如何吸附；版本、Effect 与发布状态全部来自真实资产契约。
          </span>
        </div>
        <button
          type="button"
          disabled={!canCreate}
          title={canCreate ? "创建稳定 DataAtom 身份" : "需要资产维护权限"}
          onClick={() => setCreateOpen(true)}
        >
          <Plus size={16} /> 制造原子
        </button>
      </header>

      <form className={styles.search} onSubmit={submitSearch}>
        <Search size={17} aria-hidden="true" />
        <input
          name="q"
          defaultValue={searchParams.get("q") ?? ""}
          placeholder="描述你需要的能力，例如：创建一个已绑定来访单的客户"
          aria-label="搜索原子"
        />
        <button
          type="button"
          disabled
          title="AI 组合推荐 API 尚未开放"
        >
          <Sparkles size={14} /> 让 AI 推荐组合
        </button>
      </form>

      {!selected ? (
        <EmptyState
          title={query ? "没有匹配的原子" : "还没有 DataAtom"}
          detail={
            query
              ? "请调整搜索条件。"
              : "具有资产维护权限的成员可以创建第一个稳定原子身份。"
          }
        />
      ) : (
        <section className={styles.workspace}>
          <div className={styles.catalog}>
            <div className={`${styles.grid} ${styles.atomGrid}`}>
              {atoms.map((atom) => (
                <Link
                  className={`${styles.card} ${atom.id === selected.id ? styles.selected : ""}`}
                  href={atomHref(atom.id)}
                  key={atom.id}
                >
                  <span>{atom.key}</span>
                  <Atom size={20} />
                  <strong>{atom.name}</strong>
                  <small>
                    {atom.version} · {atom.domain}
                  </small>
                  <em>{atom.versionState}</em>
                </Link>
              ))}
            </div>
          </div>

          <aside className={`${styles.detail} ${styles.light}`}>
            <header>
              <span>
                <Atom size={26} />
              </span>
              <em>{selected.versionState}</em>
            </header>
            <p>
              {selected.key} · {selected.version}
            </p>
            <h2>{selected.name}</h2>
            <span className={styles.description}>{selected.description}</span>
            <div className={styles.meta}>
              <div>
                <span>业务域</span>
                <strong>{selected.domain}</strong>
              </div>
              <div>
                <span>Effect</span>
                <strong>{selected.effect}</strong>
              </div>
              <div>
                <span>Cleanup</span>
                <strong>{selected.cleanupCapable ? "CAPABLE" : "NOT DECLARED"}</strong>
              </div>
            </div>
            <div className={styles.ports}>
              <span>输入端口</span>
              <div>
                {selected.inputPorts.length
                  ? selected.inputPorts.map((port) => <i key={port}>{port}</i>)
                  : <i>无输入端口</i>}
              </div>
            </div>
            <div className={styles.ports}>
              <span>输出端口</span>
              <div>
                {selected.outputPorts.length
                  ? selected.outputPorts.map((port) => <i key={port}>{port}</i>)
                  : <i>无输出端口</i>}
              </div>
            </div>
            <Link
              className={styles.primary}
              href={`${basePath}/fixtures/assets?atomId=${selected.id}`}
            >
              拿去组装 <ArrowRight size={16} />
            </Link>
          </aside>
        </section>
      )}

      <div className={styles.targetBar}>
        <Boxes size={17} />
        <div>
          <span>快速装配</span>
          <strong>把选中的真实原子带入资产目录</strong>
        </div>
        <Link href={`${basePath}/fixtures/assets`}>
          打开资产 <ArrowRight size={14} />
        </Link>
      </div>

      <CreateAssetDialog
        projectId={projectId}
        kind="atom"
        open={createOpen}
        onClose={() => setCreateOpen(false)}
      />
    </div>
  );
}
