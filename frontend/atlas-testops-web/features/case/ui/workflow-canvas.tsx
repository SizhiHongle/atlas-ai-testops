"use client";

import { ArrowRight, GitBranch, ShieldCheck } from "lucide-react";
import Link from "next/link";
import {
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent
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
  onNodeMove: (nodeId: string, x: number, y: number) => Promise<void>;
};

type NodePosition = { x: number; y: number };

type DragState = {
  nodeId: string;
  pointerId: number;
  startX: number;
  startY: number;
  originX: number;
  originY: number;
  moved: boolean;
};

export function WorkflowCanvas({
  nodes,
  edges,
  width,
  height,
  selectedNodeId,
  nodeHref,
  editable,
  onNodeMove
}: Readonly<WorkflowCanvasProps>) {
  const [positions, setPositions] = useState<Record<string, NodePosition>>({});
  const [draggingNodeId, setDraggingNodeId] = useState<string | null>(null);
  const dragRef = useRef<DragState | null>(null);
  const suppressClickRef = useRef<string | null>(null);

  if (!nodes.length) {
    return (
      <div className={styles.emptyGraph}>
        <GitBranch size={24} />
        <strong>WorkflowDraft 还是空图</strong>
        <p>空图由后端明确标记为 INVALID，不会被前端伪装成可运行流程。</p>
      </div>
    );
  }

  const displayNodes = nodes.map((node) => ({
    ...node,
    ...(positions[node.id] ?? { x: node.x, y: node.y })
  }));
  const nodeById = new Map(displayNodes.map((node) => [node.id, node]));

  function beginDrag(
    event: ReactPointerEvent<HTMLAnchorElement>,
    nodeId: string,
    position: NodePosition
  ) {
    if (!editable || event.button !== 0) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    dragRef.current = {
      nodeId,
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      originX: position.x,
      originY: position.y,
      moved: false
    };
    setDraggingNodeId(nodeId);
  }

  function moveDrag(event: ReactPointerEvent<HTMLAnchorElement>) {
    const drag = dragRef.current;
    if (!editable || !drag || drag.pointerId !== event.pointerId) return;
    const deltaX = event.clientX - drag.startX;
    const deltaY = event.clientY - drag.startY;
    if (Math.abs(deltaX) + Math.abs(deltaY) > 3) drag.moved = true;
    if (!drag.moved) return;
    event.preventDefault();
    setPositions((current) => ({
      ...current,
      [drag.nodeId]: {
        x: Math.max(0, Math.min(width - 170, Math.round(drag.originX + deltaX))),
        y: Math.max(0, Math.min(height - 104, Math.round(drag.originY + deltaY)))
      }
    }));
  }

  function endDrag(event: ReactPointerEvent<HTMLAnchorElement>) {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    dragRef.current = null;
    setDraggingNodeId(null);
    if (!drag.moved) return;

    const position = {
      x: Math.max(
        0,
        Math.min(
          width - 170,
          Math.round(drag.originX + event.clientX - drag.startX)
        )
      ),
      y: Math.max(
        0,
        Math.min(
          height - 104,
          Math.round(drag.originY + event.clientY - drag.startY)
        )
      )
    };
    setPositions((current) => ({
      ...current,
      [drag.nodeId]: position
    }));
    suppressClickRef.current = drag.nodeId;
    void onNodeMove(drag.nodeId, position.x, position.y).catch(() => {
      setPositions((current) => ({
        ...current,
        [drag.nodeId]: { x: drag.originX, y: drag.originY }
      }));
    });
    window.setTimeout(() => {
      suppressClickRef.current = null;
    }, 0);
  }

  function cancelDrag(event: ReactPointerEvent<HTMLAnchorElement>) {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    dragRef.current = null;
    setDraggingNodeId(null);
    setPositions((current) => ({
      ...current,
      [drag.nodeId]: { x: drag.originX, y: drag.originY }
    }));
  }

  return (
    <div className={styles.canvasScroller}>
      <div className={styles.canvas} style={{ width, height }}>
        <svg
          aria-hidden="true"
          width={width}
          height={height}
          className={styles.edges}
        >
          {edges.map((edge) => {
            const source = nodeById.get(edge.sourceNodeId);
            const target = nodeById.get(edge.targetNodeId);
            if (!source || !target) return null;
            const sourceX = source.x + 170;
            const sourceY = source.y + 52;
            const targetX = target.x;
            const targetY = target.y + 52;
            const midpoint = (sourceX + targetX) / 2;
            return (
              <g key={edge.id}>
                <path
                  d={`M ${sourceX} ${sourceY} C ${midpoint} ${sourceY}, ${midpoint} ${targetY}, ${targetX} ${targetY}`}
                />
                <text x={midpoint - 20} y={(sourceY + targetY) / 2 - 7}>
                  {edge.semanticType}
                </text>
              </g>
            );
          })}
        </svg>

        {displayNodes.map((node, index) => (
          <Link
            className={`${styles.node} ${selectedNodeId === node.id ? styles.nodeSelected : ""} ${editable ? styles.nodeEditable : ""} ${draggingNodeId === node.id ? styles.nodeDragging : ""}`}
            data-phase={node.phase}
            draggable={false}
            href={nodeHref(node.id)}
            key={node.id}
            style={{ left: node.x, top: node.y }}
            onClick={(event) => {
              if (suppressClickRef.current === node.id) event.preventDefault();
            }}
            onDragStart={(event) => event.preventDefault()}
            onPointerDown={(event) =>
              beginDrag(event, node.id, { x: node.x, y: node.y })
            }
            onPointerMove={moveDrag}
            onPointerUp={endDrag}
            onPointerCancel={cancelDrag}
            onKeyDown={(event) => {
              if (
                !editable ||
                !["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(
                  event.key
                )
              ) {
                return;
              }
              event.preventDefault();
              const next = {
                x: Math.max(
                  0,
                  Math.min(
                    width - 170,
                    node.x +
                      (event.key === "ArrowLeft"
                        ? -12
                        : event.key === "ArrowRight"
                          ? 12
                          : 0)
                  )
                ),
                y: Math.max(
                  0,
                  Math.min(
                    height - 104,
                    node.y +
                      (event.key === "ArrowUp"
                        ? -12
                        : event.key === "ArrowDown"
                          ? 12
                          : 0)
                  )
                )
              };
              setPositions((current) => ({
                ...current,
                [node.id]: next
              }));
              void onNodeMove(node.id, next.x, next.y).catch(() => {
                setPositions((current) => ({
                  ...current,
                  [node.id]: { x: node.x, y: node.y }
                }));
              });
            }}
            title={
              editable
                ? "拖拽或使用方向键保存布局；点击选择 Node"
                : undefined
            }
          >
            <span>{String(index + 1).padStart(2, "0")}</span>
            {node.terminal ? (
              <ShieldCheck size={18} aria-hidden="true" />
            ) : (
              <GitBranch size={18} aria-hidden="true" />
            )}
            <strong>{node.kind}</strong>
            <small>{node.versionRef}</small>
            <i>
              {node.inputPorts.length} IN <ArrowRight size={10} />{" "}
              {node.outputPorts.length} OUT
            </i>
          </Link>
        ))}
      </div>
    </div>
  );
}
