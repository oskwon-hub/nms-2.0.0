import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ReactFlow, {
  applyNodeChanges,
  Background,
  Controls,
  Edge,
  MarkerType,
  Node,
  NodeChange,
  NodeTypes,
  ReactFlowInstance,
} from "reactflow";
import "reactflow/dist/style.css";
import { Topology, TopologyLink, TopologyNode } from "../api/client";
import { DisplayEdge, DisplayNode, GroupBy, buildDrilldownGraph, groupKeyOf } from "../lib/topologyDrilldown";
import { computeDrilldownLayout, DrilldownLayoutMode } from "../lib/topologyDrilldownLayout";
import { ROLE_COLOR } from "./TopologyGraphView";
import RoleCloudNode from "./RoleCloudNode";

// [KOS20260923] 사용자 지적 사항에 대한 재작업:
// 1) "모든 노드/링크가 다 그려져 느리다, 단계별로 펼치지 않는다" ->
//    buildDrilldownGraph()로 접힌 그룹은 구름 1개로, 펼친 그룹만 개별 장비로
//    그려 기본 화면의 렌더링 노드 수를 크게 줄인다(성능 + 점진적 노출 동시 해결).
// 2) "그래프가 기존 Topology와 같은 모양이다" -> 3-스타일 각각 레이아웃 계산
//    자체를 다르게 하고(계층형 행/방사형 동심원/force), 엣지 type도 스타일마다
//    다르게 해(smoothstep/default curve/animated glow) 기존 Topology의
//    bendable 엣지·사각 카드와 시각적으로 구분되게 한다.
// 3) "노드마다 아이콘이 반영 안 됐다" -> DEVICE_TYPE_ICON으로 device_type별
//    아이콘을 노드 라벨에 포함한다.
// 4) "Navigator는 Role별/Device Type별로 선택해서 볼 수 있도록" -> 구름으로
//    묶는 기준(groupBy)을 prop으로 받아 device_role/device_type 중 선택한다.
export type GraphStyle = 1 | 2 | 3;

export const GRAPH_STYLE_LABEL: Record<GraphStyle, string> = {
  1: "Style 1 · 계층형 카드",
  2: "Style 2 · 방사형 Org Map",
  3: "Style 3 · 다크 트래픽 뷰",
};

const STYLE_LAYOUT: Record<GraphStyle, DrilldownLayoutMode> = {
  1: "hierarchical",
  2: "radial",
  3: "force",
};

const STYLE_EDGE_TYPE: Record<GraphStyle, string> = {
  1: "smoothstep",
  2: "default",
  3: "default",
};

const STYLE_THEME: Record<GraphStyle, { background: string; edgeColor: string; textColor: string; dotColor: string }> = {
  1: { background: "#f8fafc", edgeColor: "#64748b", textColor: "#0f172a", dotColor: "#cbd5e1" },
  2: { background: "#ffffff", edgeColor: "#94a3b8", textColor: "#0f172a", dotColor: "#e2e8f0" },
  3: { background: "#0b1120", edgeColor: "#38bdf8", textColor: "#e2e8f0", dotColor: "#1e293b" },
};

// 8장(device_type.py) 분류 결과와 1:1로 대응하는 아이콘. 새 device_type이 추가돼도
// 깨지지 않도록 deviceIcon()에서 미매핑 시 device_role 기반 fallback을 둔다.
const DEVICE_TYPE_ICON: Record<string, string> = {
  ROUTER: "🌐",
  L2_SWITCH: "🔀",
  L2_POE_SWITCH: "🔀",
  L3_SWITCH: "🔀",
  L3_POE_SWITCH: "🔀",
  ACCESS_POINT: "📶",
  IP_CAMERA: "📷",
  HUB: "🔌",
  WINDOWS_PC: "🖥️",
  LINUX_PC: "🖥️",
  MAC_PC: "🖥️",
  UNKNOWN: "❓",
};

function deviceIcon(n: TopologyNode): string {
  if (DEVICE_TYPE_ICON[n.device_type]) return DEVICE_TYPE_ICON[n.device_type];
  if (n.device_role === "SERVER") return "🖧";
  if (n.device_role === "ENDPOINT") return "🖥️";
  return "❓";
}

const ROLE_CLOUD_LABEL: Record<string, string> = {
  CORE_SWITCH: "Core Switch",
  DISTRIBUTION_SWITCH: "Distribution Switch",
  FLOOR_SWITCH: "Floor Switch",
  ACCESS_SWITCH: "Access Switch",
  EDGE_DEVICE: "Edge Device",
  SERVER: "Server",
  ENDPOINT: "Endpoint",
  UNKNOWN: "Unknown",
};

const DEVICE_TYPE_CLOUD_LABEL: Record<string, string> = {
  ROUTER: "Router",
  L3_SWITCH: "L3 Switch",
  L3_POE_SWITCH: "L3 PoE Switch",
  L2_SWITCH: "L2 Switch",
  L2_POE_SWITCH: "L2 PoE Switch",
  ACCESS_POINT: "Access Point",
  HUB: "Hub",
  IP_CAMERA: "IP Camera",
  WINDOWS_PC: "Windows PC",
  LINUX_PC: "Linux PC",
  MAC_PC: "Mac PC",
  UNKNOWN: "Unknown",
};

function cloudLabel(group: string, groupBy: GroupBy): string {
  const map = groupBy === "DEVICE_TYPE" ? DEVICE_TYPE_CLOUD_LABEL : ROLE_CLOUD_LABEL;
  const icon = groupBy === "DEVICE_TYPE" ? DEVICE_TYPE_ICON[group] : undefined;
  const label = map[group] ?? group;
  return icon ? `${icon} ${label}` : label;
}

function cloudColor(group: string, groupBy: GroupBy): string {
  if (groupBy === "DEVICE_TYPE") {
    // Device Type 그룹은 ROLE_COLOR에 없으므로, 관리 인프라(라우터/스위치)는
    // 파란 계열, 단말류는 회색 계열로 구분되게 고정 팔레트를 쓴다.
    const infraTypes = new Set(["ROUTER", "L3_SWITCH", "L3_POE_SWITCH", "L2_SWITCH", "L2_POE_SWITCH"]);
    if (infraTypes.has(group)) return "#2563eb";
    if (group === "ACCESS_POINT" || group === "HUB") return "#7c3aed";
    if (group === "IP_CAMERA") return "#dc2626";
    return "#6b7280";
  }
  return ROLE_COLOR[group] ?? "#9ca3af";
}

const STP_BLOCKED_COLOR = "#9ca3af";
const STP_ROOT_BORDER = "#f59e0b";
// 선택 노드 식별성 개선: "선택된 노드 배경을 대비색으로 반전해 달라" 요청에 따라
// 평상시(배경=색상/글자=흰색)를 선택 시 뒤집는다(배경=흰색/글자=색상) + 눈에
// 띄는 골드 글로우 테두리를 추가한다. 선택된 링크도 같은 골드 색으로 강조한다
// ("선이 선택된 것을 알 수 있도록").
const SELECTED_GLOW = "rgba(250, 204, 21, 0.65)";
const HIGHLIGHT_EDGE_COLOR = "#facc15";
// 선택 노드에 직접 연결된 링크는 기본 회색/파란 링크 및 클릭한 링크의 골드색과
// 동시에 구분되는 마젠타로 표시한다. STP 차단선의 점선 패턴은 그대로 유지한다.
const CONNECTED_EDGE_COLOR = "#ec4899";
const CONNECTED_EDGE_GLOW = "drop-shadow(0 0 3px rgba(236, 72, 153, 0.7))";

function isLinkStpBlocked(link: TopologyLink): boolean {
  return link.src_stp_state === "BLOCKING" || link.dst_stp_state === "BLOCKING";
}

function isEdgeStpBlocked(e: DisplayEdge): boolean {
  return e.links.length > 0 && e.links.every(isLinkStpBlocked);
}

function edgeLabel(e: DisplayEdge): string {
  if (e.count > 1) return `${e.count}개 링크`;
  const l = e.links[0];
  return l.label?.trim() || `${l.source} (${l.confidence})`;
}

const NODE_TYPES: NodeTypes = { cloud: RoleCloudNode };

// [KOS20260923] "노드 이동이 안 된다" 요청 - React Flow는 nodes를 매 렌더마다
// 새로 계산해 prop으로 넘기면 드래그로 옮긴 위치가 다음 렌더에서 레이아웃
// 계산값으로 되돌아간다. TopologyGraphView.tsx가 이미 같은 문제를 "위치를
// 포함한 노드 배열을 별도 state로 관리하고, 구조가 바뀔 때만(useEffect)
// 다시 계산하며, 선택 강조 같은 부가 스타일은 position은 건드리지 않고
// 별도 useMemo에서 덧씌운다"로 풀고 있어 같은 패턴을 그대로 따른다.
function buildBaseNodes(
  displayNodes: DisplayNode[],
  positions: Map<string, { x: number; y: number }>,
  circularNodes: boolean,
  groupBy: GroupBy,
): Node[] {
  return displayNodes.map((dn) => {
    const pos = positions.get(dn.id) ?? { x: 0, y: 0 };
    if (dn.kind === "cloud") {
      return {
        id: dn.id,
        type: "cloud",
        position: pos,
        data: {
          kind: "cloud",
          label: cloudLabel(dn.group, groupBy),
          color: cloudColor(dn.group, groupBy),
          count: dn.count,
          onlineCount: dn.onlineCount,
        },
      };
    }
    const n = dn.node;
    const roleColor = ROLE_COLOR[n.device_role] ?? "#9ca3af";
    const label = (
      <div
        title={n.hostname || n.management_ip || undefined}
        style={{
          display: "flex",
          flexDirection: circularNodes ? "column" : "row",
          alignItems: "center",
          gap: 4,
          justifyContent: "center",
        }}
      >
        <span style={{ fontSize: circularNodes ? 20 : 15, lineHeight: 1 }}>{deviceIcon(n)}</span>
        <span
          style={{
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
            maxWidth: circularNodes ? 76 : 120,
          }}
        >
          {n.hostname || n.management_ip || `#${n.id}`}
          {n.is_stp_root ? " ★" : ""}
        </span>
      </div>
    );
    return {
      id: dn.id,
      position: pos,
      data: { kind: "device", deviceId: n.id, label, roleColor, isStpRoot: !!n.is_stp_root },
      style: {
        background: roleColor,
        color: "white",
        borderRadius: circularNodes ? 999 : 8,
        padding: 8,
        width: circularNodes ? 90 : 170,
        height: circularNodes ? 90 : undefined,
        display: circularNodes ? "flex" : undefined,
        alignItems: circularNodes ? "center" : undefined,
        justifyContent: circularNodes ? "center" : undefined,
        textAlign: "center",
        fontSize: 11,
        fontWeight: 400,
        border: n.is_stp_root ? `3px solid ${STP_ROOT_BORDER}` : "1px solid rgba(0,0,0,0.15)",
        boxShadow: undefined,
      },
    };
  });
}

function buildFlowEdges(
  displayEdges: DisplayEdge[],
  style: GraphStyle,
  theme: { background: string; edgeColor: string; textColor: string },
): Edge[] {
  return displayEdges.map((e) => {
    const blocked = isEdgeStpBlocked(e);
    const label = e.count === 1 && blocked ? `${edgeLabel(e)} (STP Blocked)` : edgeLabel(e);
    return {
      id: e.id,
      source: e.sourceId,
      target: e.targetId,
      type: STYLE_EDGE_TYPE[style],
      label,
      animated: style === 3 && !blocked,
      data: { links: e.links },
      style: {
        stroke: blocked ? STP_BLOCKED_COLOR : theme.edgeColor,
        strokeWidth: Math.max(1.5, Math.min(6, e.count * 1.5)),
        strokeDasharray: blocked ? "4 3" : undefined,
      },
      labelStyle: { fill: theme.textColor, fontSize: 10 },
      labelBgStyle: { fill: theme.background, fillOpacity: 0.85 },
      markerEnd: { type: MarkerType.ArrowClosed, color: blocked ? STP_BLOCKED_COLOR : theme.edgeColor },
    };
  });
}

interface NewTopologyCanvasProps {
  topology: Topology;
  style: GraphStyle;
  groupBy: GroupBy;
  expandedGroups: Set<string>;
  onToggleGroup: (group: string) => void;
  selectedNodeId: number | null;
  onNodeClick: (node: TopologyNode) => void;
  onNodeContextMenu: (node: TopologyNode, x: number, y: number) => void;
  // [KOS20260923] "링크를 클릭하면 링크 정보도 조회되도록" 요청 - 구름 단계에서는
  // 여러 실제 링크가 하나의 DisplayEdge로 뭉쳐 있으므로 배열로 전달한다.
  selectedEdgeId: string | null;
  onEdgeClick: (links: TopologyLink[], edgeId: string) => void;
  onPaneClick: () => void;
}

export default function NewTopologyCanvas({
  topology,
  style,
  groupBy,
  expandedGroups,
  onToggleGroup,
  selectedNodeId,
  onNodeClick,
  onNodeContextMenu,
  selectedEdgeId,
  onEdgeClick,
  onPaneClick,
}: NewTopologyCanvasProps) {
  const theme = STYLE_THEME[style];
  const circularNodes = style === 2;

  const baseGraph = useMemo(
    () => buildDrilldownGraph(topology, expandedGroups, groupBy),
    [topology, expandedGroups, groupBy],
  );

  const [nodes, setNodes] = useState<Node[]>([]);
  const [edges, setEdges] = useState<Edge[]>([]);

  // 구조(펼침 상태/뷰 필터로 걸러진 topology)나 스타일(레이아웃 알고리즘)이
  // 바뀔 때만 좌표를 다시 계산한다 - selectedNodeId는 여기 의존성에 없으므로
  // 노드를 클릭해 선택해도 드래그한 위치가 초기화되지 않는다.
  useEffect(() => {
    const positions = computeDrilldownLayout(baseGraph.nodes, baseGraph.edges, STYLE_LAYOUT[style], groupBy);
    setNodes(buildBaseNodes(baseGraph.nodes, positions, circularNodes, groupBy));
    setEdges(buildFlowEdges(baseGraph.edges, style, theme));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [baseGraph, style, circularNodes, groupBy]);

  const onNodesChange = useCallback((changes: NodeChange[]) => {
    setNodes((current) => applyNodeChanges(changes, current));
  }, []);

  // [KOS20260923] "노드 선택 시 숨어 있는 영역에 있으면 보이는 위치로 그래프를
  // 이동한다" 요청 - Navigator에서 화면 밖에 있거나 방금 구름에서 펼쳐진 노드를
  // 고르면, 실제로 그 위치가 nodes state에 반영된 뒤(펼침으로 인한 구조 재계산은
  // 비동기라 한 템포 늦게 반영될 수 있다) 화면 밖에 있을 때만 중심으로 이동시킨다.
  // 이미 일부라도 보이는 노드는 현재 사용자가 보고 있는 화면 위치를 유지한다.
  // lastCenteredNodeIdRef로 "이미 이 선택에 대해 이동을 마쳤는지"를 기억해, 드래그
  // 등으로 nodes 배열이 계속 바뀌어도 같은 선택에 대해 반복해서 이동하지 않는다.
  const reactFlowInstanceRef = useRef<ReactFlowInstance | null>(null);
  const canvasRef = useRef<HTMLDivElement | null>(null);
  const lastCenteredNodeIdRef = useRef<number | null>(null);
  useEffect(() => {
    if (selectedNodeId == null) {
      lastCenteredNodeIdRef.current = null;
      return;
    }
    if (lastCenteredNodeIdRef.current === selectedNodeId) return;
    const target = nodes.find((n) => n.data?.kind === "device" && n.data.deviceId === selectedNodeId);
    const instance = reactFlowInstanceRef.current;
    if (!target || !instance) return;
    const width = (target.style?.width as number) ?? 170;
    const height = (target.style?.height as number) ?? 46;
    const absolutePosition = target.positionAbsolute ?? target.position;
    const topLeft = instance.flowToScreenPosition(absolutePosition);
    const bottomRight = instance.flowToScreenPosition({
      x: absolutePosition.x + width,
      y: absolutePosition.y + height,
    });
    const canvasRect = canvasRef.current?.getBoundingClientRect();
    const isVisible = canvasRect != null
      && bottomRight.x >= canvasRect.left
      && topLeft.x <= canvasRect.right
      && bottomRight.y >= canvasRect.top
      && topLeft.y <= canvasRect.bottom;
    if (isVisible) {
      lastCenteredNodeIdRef.current = selectedNodeId;
      return;
    }
    const zoom = instance.getViewport().zoom;
    instance.setCenter(target.position.x + width / 2, target.position.y + height / 2, { zoom, duration: 500 });
    lastCenteredNodeIdRef.current = selectedNodeId;
  }, [nodes, selectedNodeId]);

  // 선택 강조는 position을 건드리지 않고 배경/테두리만 덧씌운다.
  const displayNodes = useMemo(() => {
    return nodes.map((node) => {
      if (node.data?.kind !== "device" || node.data.deviceId !== selectedNodeId) return node;
      const roleColor = node.data.roleColor as string;
      return {
        ...node,
        style: {
          ...node.style,
          background: "#ffffff",
          color: roleColor,
          fontWeight: 700,
          border: node.data.isStpRoot ? `3px solid ${STP_ROOT_BORDER}` : `3px solid ${roleColor}`,
          boxShadow: `0 0 0 4px ${SELECTED_GLOW}`,
        },
      };
    });
  }, [nodes, selectedNodeId]);

  // 선택 노드에 연결된 실제 링크를 모두 마젠타로 강조한다. 구름 단계에서 여러
  // 링크가 하나로 집계된 Edge도 data.links를 검사하므로 선택 장비가 포함된 선은
  // 빠짐없이 강조된다. 사용자가 직접 클릭한 링크는 기존 골드 강조를 우선한다.
  const displayEdges = useMemo(() => {
    return edges.map((e) => {
      if (e.id === selectedEdgeId) {
        return {
          ...e,
          zIndex: 1000,
          style: { ...e.style, stroke: HIGHLIGHT_EDGE_COLOR, strokeWidth: Math.max(4, ((e.style?.strokeWidth as number) ?? 2) + 2) },
          labelStyle: { ...e.labelStyle, fontWeight: 700 },
          markerEnd: { type: MarkerType.ArrowClosed, color: HIGHLIGHT_EDGE_COLOR },
        };
      }
      const connected = selectedNodeId != null && (e.data as { links?: TopologyLink[] } | undefined)?.links?.some(
        (link) => link.src_device_id === selectedNodeId || link.dst_device_id === selectedNodeId,
      );
      if (!connected) return e;
      return {
        ...e,
        zIndex: 500,
        style: {
          ...e.style,
          stroke: CONNECTED_EDGE_COLOR,
          strokeWidth: Math.max(3.5, ((e.style?.strokeWidth as number) ?? 2) + 1.5),
          filter: CONNECTED_EDGE_GLOW,
        },
        labelStyle: { ...e.labelStyle, fill: CONNECTED_EDGE_COLOR, fontWeight: 700 },
        markerEnd: { type: MarkerType.ArrowClosed, color: CONNECTED_EDGE_COLOR },
      };
    });
  }, [edges, selectedEdgeId, selectedNodeId]);

  return (
    <div ref={canvasRef} style={{ width: "100%", height: "100%", background: theme.background }}>
      <ReactFlow
        nodes={displayNodes}
        edges={displayEdges}
        nodeTypes={NODE_TYPES}
        onInit={(instance) => {
          reactFlowInstanceRef.current = instance;
        }}
        onNodesChange={onNodesChange}
        onNodeClick={(_, node) => {
          if (node.id.startsWith("cloud:")) {
            onToggleGroup(node.id.slice("cloud:".length));
            return;
          }
          const deviceId = Number(node.id.slice("device:".length));
          const found = topology.nodes.find((n) => n.id === deviceId);
          if (found) onNodeClick(found);
        }}
        onNodeContextMenu={(event, node) => {
          event.preventDefault();
          if (node.id.startsWith("cloud:")) {
            onToggleGroup(node.id.slice("cloud:".length));
            return;
          }
          const deviceId = Number(node.id.slice("device:".length));
          const found = topology.nodes.find((n) => n.id === deviceId);
          if (found) onNodeContextMenu(found, event.clientX, event.clientY);
        }}
        onEdgeClick={(_, edge) => {
          const links = (edge.data as { links?: TopologyLink[] } | undefined)?.links;
          if (links && links.length > 0) onEdgeClick(links, edge.id);
        }}
        onPaneClick={onPaneClick}
        panOnDrag
        zoomOnScroll
        zoomOnPinch
        minZoom={0.1}
        maxZoom={2.5}
        fitView
        fitViewOptions={{ padding: 0.2 }}
      >
        <Background color={theme.dotColor} />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}
