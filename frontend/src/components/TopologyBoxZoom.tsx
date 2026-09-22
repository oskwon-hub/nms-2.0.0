import {
  CSSProperties,
  MouseEvent as ReactMouseEvent,
  ReactNode,
  RefObject,
  useEffect,
  useRef,
  useState,
} from "react";
import { ReactFlowInstance } from "reactflow";

const MIN_ZOOM = 0.5;
const MAX_ZOOM = 2;
const MIN_DRAG_SIZE = 8;

interface Point {
  x: number;
  y: number;
}

interface DragState {
  start: Point;
  current: Point;
  zoomOut: boolean;
}

interface TopologyBoxZoomProps {
  children: ReactNode;
  flowInstanceRef: RefObject<ReactFlowInstance | null>;
  className?: string;
  style?: CSSProperties;
}

/** Topology 그래프 공통 우클릭 영역 확대/축소 컨테이너. */
export default function TopologyBoxZoom({
  children,
  flowInstanceRef,
  className,
  style,
}: TopologyBoxZoomProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<DragState | null>(null);
  const [drag, setDrag] = useState<DragState | null>(null);

  useEffect(() => {
    const handleMouseMove = (event: MouseEvent) => {
      const active = dragRef.current;
      const container = containerRef.current;
      if (!active || !container) return;

      event.preventDefault();
      const bounds = container.getBoundingClientRect();
      const current = {
        x: Math.min(Math.max(event.clientX - bounds.left, 0), bounds.width),
        y: Math.min(Math.max(event.clientY - bounds.top, 0), bounds.height),
      };
      const next = { ...active, current };
      dragRef.current = next;
      setDrag(next);
    };

    const handleMouseUp = (event: MouseEvent) => {
      const active = dragRef.current;
      const container = containerRef.current;
      dragRef.current = null;
      setDrag(null);
      if (!active || !container || event.button !== 2) return;

      event.preventDefault();
      const width = Math.abs(active.current.x - active.start.x);
      const height = Math.abs(active.current.y - active.start.y);
      if (width < MIN_DRAG_SIZE || height < MIN_DRAG_SIZE) return;

      const instance = flowInstanceRef.current;
      if (!instance) return;

      const bounds = container.getBoundingClientRect();
      const center = {
        x: bounds.left + (active.start.x + active.current.x) / 2,
        y: bounds.top + (active.start.y + active.current.y) / 2,
      };
      const flowCenter = instance.screenToFlowPosition(center);
      const scale = Math.min(bounds.width / width, bounds.height / height);
      const currentZoom = instance.getZoom();
      const requestedZoom = active.zoomOut ? currentZoom / scale : currentZoom * scale;
      const zoom = Math.min(Math.max(requestedZoom, MIN_ZOOM), MAX_ZOOM);

      instance.setCenter(flowCenter.x, flowCenter.y, { zoom, duration: 250 });
    };

    window.addEventListener("mousemove", handleMouseMove, { passive: false });
    window.addEventListener("mouseup", handleMouseUp, { passive: false });
    return () => {
      window.removeEventListener("mousemove", handleMouseMove);
      window.removeEventListener("mouseup", handleMouseUp);
    };
  }, [flowInstanceRef]);

  const handleMouseDownCapture = (event: ReactMouseEvent<HTMLDivElement>) => {
    if (event.button !== 2) return;
    if ((event.target as Element).closest(".react-flow__node, .react-flow__edge")) return;
    event.preventDefault();
    event.stopPropagation();

    const bounds = event.currentTarget.getBoundingClientRect();
    const start = { x: event.clientX - bounds.left, y: event.clientY - bounds.top };
    const next = { start, current: start, zoomOut: event.shiftKey };
    dragRef.current = next;
    setDrag(next);
  };

  const selection = drag
    ? {
        left: Math.min(drag.start.x, drag.current.x),
        top: Math.min(drag.start.y, drag.current.y),
        width: Math.abs(drag.current.x - drag.start.x),
        height: Math.abs(drag.current.y - drag.start.y),
      }
    : null;

  return (
    <div
      ref={containerRef}
      className={className}
      style={{ ...style, position: "relative" }}
      onMouseDownCapture={handleMouseDownCapture}
      onContextMenuCapture={(event) => {
        event.preventDefault();
      }}
    >
      {children}
      {drag && selection && (
        <div
          aria-hidden="true"
          style={{
            position: "absolute",
            zIndex: 20,
            pointerEvents: "none",
            ...selection,
            border: `2px solid ${drag.zoomOut ? "#f97316" : "#2563eb"}`,
            background: drag.zoomOut ? "rgba(249, 115, 22, 0.12)" : "rgba(37, 99, 235, 0.12)",
          }}
        >
          <span
            style={{
              position: "absolute",
              top: 4,
              left: 6,
              padding: "2px 5px",
              borderRadius: 3,
              color: "white",
              background: drag.zoomOut ? "#f97316" : "#2563eb",
              fontSize: 11,
              fontWeight: 600,
              whiteSpace: "nowrap",
            }}
          >
            {drag.zoomOut ? "축소" : "확대"}
          </span>
        </div>
      )}
    </div>
  );
}
