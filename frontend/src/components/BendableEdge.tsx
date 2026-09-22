import { useCallback, useRef, useState } from "react";
import { EdgeProps, useReactFlow } from "reactflow";

export interface BendPoint {
  x: number;
  y: number;
}

export interface BendableEdgeData {
  bend?: BendPoint;
  onBendChange?: (edgeId: string, bend: BendPoint) => void;
}

// [KOS20260921] 링크가 겹치거나 복잡하게 얽힌 구간에서 특정 선 하나를 눈으로
// 따라가기 어렵다는 요청에 따라, 기본 직선 Edge 대신 선을 클릭+드래그하면
// 두 끝점(연결된 노드)은 고정한 채 중간 지점만 옮겨 선 모양을 바꿀 수 있게 한다.
// 굽은 지점은 edge.data.bend에 저장하고, 저장값이 없으면 두 노드의 중점을 쓴다
// (재배치 시 edges 자체가 buildGraph()로 새로 만들어지므로 자연히 초기화된다).
export default function BendableEdge({ id, sourceX, sourceY, targetX, targetY, style, markerEnd, label, data }: EdgeProps) {
  const { screenToFlowPosition } = useReactFlow();
  const draggingRef = useRef(false);
  const startRef = useRef<{ x: number; y: number } | null>(null);
  const [hovered, setHovered] = useState(false);
  const [dragging, setDragging] = useState(false);

  const edgeData = data as BendableEdgeData | undefined;
  const bend = edgeData?.bend;
  const midX = bend?.x ?? (sourceX + targetX) / 2;
  const midY = bend?.y ?? (sourceY + targetY) / 2;
  // 실제 굴곡점을 통과하는 polyline이라 핸들의 위치와 사용자가 보는 선이 일치한다.
  const path = `M ${sourceX},${sourceY} L ${midX},${midY} L ${targetX},${targetY}`;

  const handleMouseDown = useCallback(
    (evt: React.MouseEvent) => {
      if (evt.button !== 0) return;
      // 캔버스 팬(panOnDrag)이 같이 반응하지 않도록 막는다 - click 이벤트는 별개로
      // 전파되므로 드래그 없이 뗀 경우 onEdgeClick(정보 패널 열기)은 그대로 동작한다.
      evt.stopPropagation();
      draggingRef.current = false;
      startRef.current = { x: evt.clientX, y: evt.clientY };

      const handleMouseMove = (moveEvt: MouseEvent) => {
        if (!startRef.current) return;
        const dx = moveEvt.clientX - startRef.current.x;
        const dy = moveEvt.clientY - startRef.current.y;
        if (!draggingRef.current && Math.hypot(dx, dy) < 4) return; // 단순 클릭과 구분
        draggingRef.current = true;
        setDragging(true);
        const pos = screenToFlowPosition({ x: moveEvt.clientX, y: moveEvt.clientY });
        edgeData?.onBendChange?.(id, pos);
      };
      const handleMouseUp = () => {
        startRef.current = null;
        draggingRef.current = false;
        setDragging(false);
        window.removeEventListener("mousemove", handleMouseMove);
        window.removeEventListener("mouseup", handleMouseUp);
      };
      window.addEventListener("mousemove", handleMouseMove);
      window.addEventListener("mouseup", handleMouseUp);
    },
    [edgeData, id, screenToFlowPosition],
  );

  return (
    <>
      <path id={id} d={path} style={style} markerEnd={markerEnd} fill="none" />
      <path
        d={path}
        fill="none"
        stroke="transparent"
        strokeWidth={16}
        style={{ cursor: dragging ? "grabbing" : "grab" }}
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
        onMouseDown={handleMouseDown}
      />
      {(hovered || dragging || bend) && (
        <circle
          cx={midX}
          cy={midY}
          r={6}
          fill="#ffffff"
          stroke="#2563eb"
          strokeWidth={2}
          style={{ cursor: dragging ? "grabbing" : "grab" }}
          onMouseEnter={() => setHovered(true)}
          onMouseLeave={() => setHovered(false)}
          onMouseDown={handleMouseDown}
        />
      )}
      {label != null && (
        <text x={midX} y={midY - 6} textAnchor="middle" style={{ fontSize: 10, fill: "#374151", pointerEvents: "none" }}>
          {label}
        </text>
      )}
    </>
  );
}
