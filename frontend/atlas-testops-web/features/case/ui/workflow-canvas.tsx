"use client";

import {
  Component,
  GitBranch,
  Grip,
  Link2,
  Maximize2,
  ShieldCheck,
  X,
  ZoomIn,
  ZoomOut
} from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
  type SyntheticEvent
} from "react";

import type {
  WorkflowEdgeViewModel,
  WorkflowNodeViewModel
} from "../model/case";
import styles from "./case-page.module.css";

type WorkflowCanvasProps = {
  nodes: WorkflowNodeViewModel[];
  edges: WorkflowEdgeViewModel[];
  width: number;
  height: number;
  selectedNodeId: string | null;
  nodeHref: (nodeId: string) => string;
  editable: boolean;
  mode: "ai" | "manual";
  draftRevision: number;
  assetHref: string;
  onNodeMove: (nodeId: string, x: number, y: number) => Promise<void>;
  onConnect: (sourceNodeId: string, targetNodeId: string) => Promise<void>;
  onDeleteEdge: (edgeId: string) => Promise<void>;
  onAutoLayout: () => Promise<void>;
};

type NodePosition = { x: number; y: number };

type NodeDragState = {
  nodeId: string;
  pointerId: number;
  startX: number;
  startY: number;
  origin: NodePosition;
  last: NodePosition;
  moved: boolean;
};

type PanDragState = {
  pointerId: number;
  startX: number;
  startY: number;
  origin: NodePosition;
};

const NODE_WIDTH = 160;
const NODE_HEIGHT = 108;

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.max(minimum, Math.min(maximum, value));
}

function nodeOutput(node: WorkflowNodeViewModel): string {
  return (
    node.outputPorts.map((port) => port.key).join(" · ") ||
    node.versionRef
  );
}

export function WorkflowCanvas({
  nodes,
  edges,
  width,
  height,
  selectedNodeId,
  nodeHref,
  editable,
  mode,
  draftRevision,
  assetHref,
  onNodeMove,
  onConnect,
  onDeleteEdge,
  onAutoLayout
}: Readonly<WorkflowCanvasProps>) {
  const router = useRouter();
  const viewportRef = useRef<HTMLDivElement>(null);
  const suppressClickRef = useRef<string | null>(null);
  const [zoom, setZoom] = useState(0.72);
  const [pan, setPan] = useState<NodePosition>({ x: 8, y: 8 });
  const [panDrag, setPanDrag] = useState<PanDragState | null>(null);
  const [drag, setDrag] = useState<NodeDragState | null>(null);
  const [positions, setPositions] = useState<Record<string, NodePosition>>({});
  const [localSelectedNodeId, setLocalSelectedNodeId] = useState<string | null>(
    selectedNodeId ?? nodes[0]?.id ?? null
  );
  const [connectingFrom, setConnectingFrom] = useState<string | null>(null);
  const stageWidth = Math.max(1180, width);
  const stageHeight = Math.max(540, height);
  const displayNodes = nodes.map((node) => ({
    ...node,
    ...(positions[node.id] ?? { x: node.x, y: node.y }),
    ...(drag?.nodeId === node.id ? drag.last : {})
  }));
  const selected =
    displayNodes.find((node) => node.id === localSelectedNodeId) ??
    displayNodes[0] ??
    null;
  const nodeById = new Map(displayNodes.map((node) => [node.id, node]));
  const selectedEdges = selected
    ? edges.filter(
        (edge) =>
          edge.sourceNodeId === selected.id ||
          edge.targetNodeId === selected.id
      )
    : [];

  function selectNode(nodeId: string) {
    setLocalSelectedNodeId(nodeId);
    router.replace(nodeHref(nodeId), { scroll: false });
  }

  function beginPan(event: ReactPointerEvent<HTMLDivElement>) {
    if (
      event.button !== 0 ||
      (event.target as HTMLElement).closest(
        "[data-canvas-node], [data-canvas-control]"
      )
    ) {
      return;
    }
    event.currentTarget.setPointerCapture(event.pointerId);
    setPanDrag({
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      origin: pan
    });
  }

  function movePan(event: ReactPointerEvent<HTMLDivElement>) {
    if (!panDrag || event.pointerId !== panDrag.pointerId) return;
    setPan({
      x: panDrag.origin.x + event.clientX - panDrag.startX,
      y: panDrag.origin.y + event.clientY - panDrag.startY
    });
  }

  function endPan(event: ReactPointerEvent<HTMLDivElement>) {
    if (!panDrag || event.pointerId !== panDrag.pointerId) return;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    setPanDrag(null);
  }

  function beginNodeDrag(
    event: ReactPointerEvent<HTMLDivElement>,
    node: WorkflowNodeViewModel
  ) {
    event.stopPropagation();
    setLocalSelectedNodeId(node.id);
    if (!editable || event.button !== 0) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    const position = positions[node.id] ?? { x: node.x, y: node.y };
    setDrag({
      nodeId: node.id,
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      origin: position,
      last: position,
      moved: false
    });
  }

  function moveNodeDrag(event: ReactPointerEvent<HTMLDivElement>) {
    if (!drag || event.pointerId !== drag.pointerId) return;
    const next = {
      x: clamp(
        drag.origin.x + (event.clientX - drag.startX) / zoom,
        18,
        stageWidth - NODE_WIDTH - 18
      ),
      y: clamp(
        drag.origin.y + (event.clientY - drag.startY) / zoom,
        18,
        stageHeight - NODE_HEIGHT - 18
      )
    };
    setDrag({
      ...drag,
      last: next,
      moved:
        drag.moved ||
        Math.abs(next.x - drag.origin.x) > 1 ||
        Math.abs(next.y - drag.origin.y) > 1
    });
  }

  function endNodeDrag(event: ReactPointerEvent<HTMLDivElement>) {
    if (!drag || event.pointerId !== drag.pointerId) return;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    const completed = drag;
    setDrag(null);
    if (!completed.moved) return;
    const next = {
      x: Math.round(completed.last.x),
      y: Math.round(completed.last.y)
    };
    setPositions((current) => ({ ...current, [completed.nodeId]: next }));
    suppressClickRef.current = completed.nodeId;
    void onNodeMove(completed.nodeId, next.x, next.y).catch(() => {
      setPositions((current) => ({
        ...current,
        [completed.nodeId]: completed.origin
      }));
    });
    window.setTimeout(() => {
      suppressClickRef.current = null;
    }, 0);
  }

  function cancelNodeDrag(event: ReactPointerEvent<HTMLDivElement>) {
    if (!drag || event.pointerId !== drag.pointerId) return;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    setDrag(null);
  }

  function moveNodeByKeyboard(
    event: ReactKeyboardEvent<HTMLDivElement>,
    node: WorkflowNodeViewModel
  ) {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      selectNode(node.id);
      return;
    }
    if (
      !editable ||
      !["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)
    ) {
      return;
    }
    event.preventDefault();
    const current = positions[node.id] ?? { x: node.x, y: node.y };
    const delta = event.shiftKey ? 32 : 12;
    const next = {
      x: clamp(
        current.x +
          (event.key === "ArrowLeft"
            ? -delta
            : event.key === "ArrowRight"
              ? delta
              : 0),
        18,
        stageWidth - NODE_WIDTH - 18
      ),
      y: clamp(
        current.y +
          (event.key === "ArrowUp"
            ? -delta
            : event.key === "ArrowDown"
              ? delta
              : 0),
        18,
        stageHeight - NODE_HEIGHT - 18
      )
    };
    setLocalSelectedNodeId(node.id);
    setPositions((value) => ({ ...value, [node.id]: next }));
    void onNodeMove(node.id, next.x, next.y).catch(() => {
      setPositions((value) => ({ ...value, [node.id]: current }));
    });
  }

  function fitView() {
    const bounds = viewportRef.current?.getBoundingClientRect();
    if (!bounds) return;
    const nextZoom = clamp(
      Math.min(
        (bounds.width - 28) / stageWidth,
        (bounds.height - 28) / stageHeight
      ),
      0.42,
      1.04
    );
    setZoom(Number(nextZoom.toFixed(2)));
    setPan({
      x: Math.max(14, (bounds.width - stageWidth * nextZoom) / 2),
      y: Math.max(14, (bounds.height - stageHeight * nextZoom) / 2)
    });
  }

  function startConnection(event: SyntheticEvent, nodeId: string) {
    event.stopPropagation();
    if (!editable) return;
    setConnectingFrom(nodeId);
    setLocalSelectedNodeId(nodeId);
  }

  function finishConnection(event: SyntheticEvent, nodeId: string) {
    event.stopPropagation();
    if (!editable || !connectingFrom || connectingFrom === nodeId) return;
    const source = connectingFrom;
    setConnectingFrom(null);
    void onConnect(source, nodeId).catch(() => undefined);
  }

  function handlePortKey(
    event: ReactKeyboardEvent<HTMLButtonElement>,
    action: () => void
  ) {
    event.stopPropagation();
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    action();
  }

  return (
    <section className={styles.graphCanvas}>
      <header className={styles.canvasTopbar}>
        <div>
          <GitBranch size={15} />
          <span>WORKFLOW GRAPH</span>
          <strong>
            {nodes.length} NODES · {edges.length} EDGES
          </strong>
        </div>
        <em data-mode={editable ? "manual" : "ai"}>
          {editable ? "端口可连接 · 连线可删除" : "AI Patch 共编模式"}
        </em>
        <small>Draft r{draftRevision}</small>
      </header>

      <div
        ref={viewportRef}
        className={`${styles.canvasViewport} ${
          panDrag ? styles.canvasPanning : ""
        }`}
        onPointerDown={beginPan}
        onPointerMove={movePan}
        onPointerUp={endPan}
        onPointerCancel={endPan}
      >
        <div className={styles.canvasGrid} />

        {nodes.length ? (
          <div
            className={styles.canvasStage}
            style={{
              width: stageWidth,
              height: stageHeight,
              transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`
            }}
          >
            <svg
              className={styles.edges}
              viewBox={`0 0 ${stageWidth} ${stageHeight}`}
              aria-hidden="true"
            >
              <defs>
                <marker
                  id="atlas-case-canvas-arrow"
                  viewBox="0 0 10 10"
                  refX="9"
                  refY="5"
                  markerWidth="6"
                  markerHeight="6"
                  orient="auto-start-reverse"
                >
                  <path d="M 0 0 L 10 5 L 0 10 z" />
                </marker>
              </defs>
              {edges.map((edge) => {
                const source = nodeById.get(edge.sourceNodeId);
                const target = nodeById.get(edge.targetNodeId);
                if (!source || !target) return null;
                const startX = source.x + NODE_WIDTH;
                const startY = source.y + NODE_HEIGHT / 2;
                const endX = target.x;
                const endY = target.y + NODE_HEIGHT / 2;
                const curve = Math.max(
                  56,
                  Math.abs(endX - startX) * 0.42
                );
                const path = `M ${startX} ${startY} C ${startX + curve} ${startY}, ${endX - curve} ${endY}, ${endX} ${endY}`;
                return (
                  <g key={edge.id}>
                    <path
                      className={styles.edgeHit}
                      d={path}
                      onClick={(event) => {
                        event.stopPropagation();
                        if (editable) {
                          void onDeleteEdge(edge.id).catch(() => undefined);
                        }
                      }}
                    >
                      <title>
                        {editable
                          ? `删除连线 ${edge.semanticType}`
                          : edge.semanticType}
                      </title>
                    </path>
                    <path className={styles.edgeShadow} d={path} />
                    <path
                      className={styles.edgeFlow}
                      d={path}
                      markerEnd="url(#atlas-case-canvas-arrow)"
                    />
                    <text
                      x={(startX + endX) / 2}
                      y={(startY + endY) / 2 - 8}
                    >
                      {edge.semanticType}
                    </text>
                  </g>
                );
              })}
            </svg>

            {displayNodes.map((node, index) => (
              <div
                role="button"
                tabIndex={0}
                aria-label={`${node.kind}，${
                  editable ? "可拖拽，方向键可移动" : "Workflow Node"
                }`}
                className={`${styles.node} ${
                  selected?.id === node.id ? styles.nodeSelected : ""
                } ${editable ? styles.nodeEditable : ""} ${
                  drag?.nodeId === node.id ? styles.nodeDragging : ""
                }`}
                data-canvas-node
                data-phase={node.phase}
                key={node.id}
                style={{ left: node.x, top: node.y }}
                onClick={() => {
                  if (suppressClickRef.current !== node.id) {
                    selectNode(node.id);
                  }
                }}
                onPointerDown={(event) => beginNodeDrag(event, node)}
                onPointerMove={moveNodeDrag}
                onPointerUp={endNodeDrag}
                onPointerCancel={cancelNodeDrag}
                onKeyDown={(event) => moveNodeByKeyboard(event, node)}
              >
                <span className={styles.nodeIndex}>
                  {String(index + 1).padStart(2, "0")}
                </span>
                <i className={styles.nodeIcon}>
                  {node.terminal ? (
                    <ShieldCheck size={18} />
                  ) : (
                    <GitBranch size={18} />
                  )}
                </i>
                <div>
                  <small>
                    {node.phase} · {node.terminal ? "Terminal" : "Node"}
                  </small>
                  <strong>{node.kind}</strong>
                  <code>{nodeOutput(node)}</code>
                </div>
                {node.inputPorts.length ? (
                  <button
                    className={`${styles.canvasPort} ${styles.portIn}`}
                    type="button"
                    tabIndex={editable ? 0 : -1}
                    aria-label={`连接到 ${node.kind}`}
                    data-canvas-control
                    disabled={!editable}
                    onPointerDown={(event) => event.stopPropagation()}
                    onClick={(event) => finishConnection(event, node.id)}
                    onKeyDown={(event) =>
                      handlePortKey(event, () =>
                        finishConnection(event, node.id)
                      )
                    }
                  />
                ) : null}
                {node.outputPorts.length ? (
                  <button
                    className={`${styles.canvasPort} ${styles.portOut} ${
                      connectingFrom === node.id ? styles.portConnecting : ""
                    }`}
                    type="button"
                    tabIndex={editable ? 0 : -1}
                    aria-label={`从 ${node.kind} 创建连线`}
                    data-canvas-control
                    disabled={!editable}
                    onPointerDown={(event) => event.stopPropagation()}
                    onClick={(event) => startConnection(event, node.id)}
                    onKeyDown={(event) =>
                      handlePortKey(event, () =>
                        startConnection(event, node.id)
                      )
                    }
                  />
                ) : null}
                <Grip className={styles.nodeDragHandle} size={13} />
              </div>
            ))}
          </div>
        ) : (
          <div className={styles.emptyGraph}>
            <GitBranch size={24} />
            <strong>WorkflowDraft 还是空图</strong>
            <p>
              空图由后端明确标记为 INVALID，不会被前端伪装成可运行流程。
            </p>
          </div>
        )}

        {connectingFrom ? (
          <div className={styles.connectHint} data-canvas-control>
            <Link2 size={13} />
            <span>选择目标节点 · 自动匹配 Typed Port</span>
            <button
              type="button"
              aria-label="取消连线"
              onClick={() => setConnectingFrom(null)}
            >
              <X size={12} />
            </button>
          </div>
        ) : null}

        <div className={styles.canvasToolrail} data-canvas-control>
          <button
            type="button"
            aria-label="缩小画布"
            disabled={!nodes.length}
            onClick={() =>
              setZoom((value) =>
                Math.max(0.42, Number((value - 0.08).toFixed(2)))
              )
            }
          >
            <ZoomOut size={15} />
          </button>
          <span>{Math.round(zoom * 100)}%</span>
          <button
            type="button"
            aria-label="放大画布"
            disabled={!nodes.length}
            onClick={() =>
              setZoom((value) =>
                Math.min(1.08, Number((value + 0.08).toFixed(2)))
              )
            }
          >
            <ZoomIn size={15} />
          </button>
          <i />
          <button
            type="button"
            aria-label="适配视图"
            disabled={!nodes.length}
            onClick={fitView}
          >
            <Maximize2 size={15} />
          </button>
          <button
            type="button"
            aria-label="自动布局"
            disabled={!editable || !nodes.length}
            onClick={() => void onAutoLayout().catch(() => undefined)}
          >
            <GitBranch size={15} />
          </button>
          <Link className={styles.canvasAssetButton} href={assetHref}>
            <Component size={14} /> 资产
          </Link>
        </div>

        {nodes.length ? (
          <div className={styles.canvasMinimap} data-canvas-control>
            <span>MINIMAP</span>
            <div>
              {displayNodes.map((node) => (
                <i
                  data-phase={node.phase}
                  key={node.id}
                  style={{
                    left: `${(node.x / stageWidth) * 100}%`,
                    top: `${(node.y / stageHeight) * 100}%`
                  }}
                />
              ))}
            </div>
          </div>
        ) : null}

        {selected ? (
          <aside className={styles.nodeInspector} data-canvas-control>
            <div>
              <span>SELECTED NODE</span>
              <em>{selected.phase}</em>
            </div>
            <strong>{selected.kind}</strong>
            <code>{nodeOutput(selected)}</code>
            <small>
              {selected.inputPorts.length} IN · {selected.outputPorts.length} OUT
              {selected.oracleStrength
                ? ` · ${selected.oracleStrength} Oracle`
                : ""}
            </small>
            <div className={styles.nodeLinks}>
              <span>PORT LINKS · {selectedEdges.length}</span>
              {selectedEdges.slice(0, 3).map((edge) => (
                <button
                  type="button"
                  disabled={!editable}
                  key={edge.id}
                  onClick={() =>
                    void onDeleteEdge(edge.id).catch(() => undefined)
                  }
                >
                  <i>
                    {edge.sourceNodeId === selected.id ? "OUT" : "IN"}
                  </i>
                  <b>{edge.semanticType}</b>
                  {editable ? <X size={10} /> : null}
                </button>
              ))}
            </div>
          </aside>
        ) : null}

        <span className={styles.canvasModeLabel}>
          {mode === "manual"
            ? editable
              ? "MANUAL · EDITABLE"
              : "MANUAL · READ ONLY"
            : "AI PATCH · READ ONLY"}
        </span>
      </div>
    </section>
  );
}
