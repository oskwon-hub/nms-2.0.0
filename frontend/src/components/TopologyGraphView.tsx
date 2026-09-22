import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ReactFlow, {
  Background,
  Controls,
  Edge,
  EdgeTypes,
  MarkerType,
  Node,
  NodeChange,
  ReactFlowInstance,
  applyNodeChanges,
} from "reactflow";
import "reactflow/dist/style.css";
import { api, DeviceDetail, LinkPingResult, Topology, TopologyLink } from "../api/client";
import { computeLayoutPositions, LAYOUT_LABELS, LayoutMode } from "../lib/topologyLayouts";
import { buildClusteredTopology, RenderNode } from "../lib/topologyClustering";
import { formatUtcDateTime } from "../lib/dateTime";
import DeviceOverviewPanel from "./DeviceOverviewPanel";
import StatusBadge from "./StatusBadge";
import BendableEdge, { BendPoint } from "./BendableEdge";
import Modal from "./Modal";
import DeviceDetailContent from "./DeviceDetailContent";
import TopologyBoxZoom from "./TopologyBoxZoom";
import TopologyContextMenu from "./TopologyContextMenu";

// edgeTypes/nodeTypes 객체는 매 렌더마다 새로 만들면 React Flow가 경고를 띄우고
// 불필요하게 다시 그리므로 컴포넌트 바깥에서 한 번만 만든다.
const EDGE_TYPES: EdgeTypes = { bendable: BendableEdge };

type PortFilter = "ALL" | "BOTH_KNOWN" | "PARTIAL";

// [KOS20260921] "선이 있는데 SRC/DST PORT가 한쪽만 나온다"는 리포트에 대응해,
// 양쪽 포트를 모두 아는 링크만 보거나 반대로 그것만 골라 볼 수 있게 한다.
// [KOS20260921] Cluster 요약 Edge(source === "CLUSTER")를 필터와 무관하게 항상
// 보여줬더니, "양쪽 포트 모두 확인된 링크만" 상태에서도 화면에 남아 클릭하면
// SRC/DST PORT가 비어 보이는 문제가 있었다 - 필터가 "지금 보이는 링크는 전부
// 포트가 확인됨"을 보장하도록, Cluster 요약 Edge도 다른 링크와 동일하게
// (포트 개념이 없으므로 항상 미확인으로) 필터를 적용한다.
function matchesPortFilter(link: TopologyLink | undefined, filter: PortFilter): boolean {
  if (!link || filter === "ALL") return true;
  const bothKnown = link.src_port != null && link.dst_port != null;
  return filter === "BOTH_KNOWN" ? bothKnown : !bothKnown;
}

// [KOS20260921] LLDP는 SRC/DST PORT가 거의 항상 확인되지만 ARP/FDB_ARP는 근거의
// 성격상(단말은 관리형 포트 자체가 없음) 포트가 잘 안 채워진다 - 그리고 이 네트워크는
// ARP/FDB_ARP 근거가 대다수라서 "양쪽 포트 모두 확인" 필터만으로는 화면에 아무것도
// 안 남는 경우가 많다. 포트 확인 여부와는 별개로, 근거(LLDP/CDP/FDB/FDB_ARP/ARP)
// 종류 자체로 링크를 고를 수 있게 한다.
// [KOS20260921] 처음엔 Cluster 요약 Edge(source === "CLUSTER")를 "실제 근거가
// 아니다"라는 이유로 이 필터에서 항상 예외 처리했는데, 그러면 "LLDP만 보기"를
// 선택해도 포트 정보가 없는 Cluster 요약선이 화면에 남아있다가 클릭되면 SRC/DST
// PORT가 비어 보이는 - 포트 필터에서 이미 한 번 겪은 것과 같은 종류의 - 혼란을
// 준다. 예외를 없애고 CLUSTER도 availableSources에 포함시켜 다른 근거와 동일하게
// 체크박스로 켜고 끌 수 있게 한다(기본은 켜짐 = 기존과 동일하게 항상 보임).
function matchesSourceFilter(link: TopologyLink | undefined, hiddenSources: Set<string>): boolean {
  if (!link) return true;
  return !hiddenSources.has(link.source);
}

// [KOS20260921] "LAG1"/"LAG 2" 같은 이름은 물리 포트 하나가 아니라 여러 포트를
// 묶은 LAG(Link Aggregation Group) 가상 인터페이스인데, 일반 포트 이름과 똑같이
// 보여주면 구분이 안 간다는 지적 - LAG 관계임을 한눈에 알 수 있도록 "LAG(번호)"
// 형태로 바꿔 표시한다. LAG가 아닌 이름은 그대로 둔다.
const LAG_NAME_PATTERN = /^LAG\s*(\d+)$/i;

function formatPortName(name: string | null | undefined): string | null {
  if (!name) return name ?? null;
  const match = name.trim().match(LAG_NAME_PATTERN);
  return match ? `LAG(${match[1]})` : name;
}

// [KOS20260922] 양쪽 포트 중 하나라도 dot1dStpPortState=BLOCKING이면 STP가
// 이 링크를 논리적으로 막아둔 것이다(물리적으로는 연결돼 있어도 트래픽이
// 흐르지 않음) - 그래프에서 이 사실을 구분해 보여준다.
function isStpBlocked(link: TopologyLink | undefined): boolean {
  if (!link) return false;
  return link.src_stp_state === "BLOCKING" || link.dst_stp_state === "BLOCKING";
}

function isLagLink(link: TopologyLink | undefined): boolean {
  if (!link) return false;
  return LAG_NAME_PATTERN.test((link.src_port ?? "").trim()) || LAG_NAME_PATTERN.test((link.dst_port ?? "").trim());
}

// [KOS20260922] "Topology 근거 표시에 STP, LAG도 추가해야 하지 않을까?" - LAG/STP는
// link.source 값이 아니라(LLDP/FDB 등과 동시에 성립할 수 있는 별개 속성) 근거
// 체크박스(hiddenSources)와 같은 Set에 넣을 수 없어, 독립된 토글 2개를 같은
// "근거 표시" 줄에 나란히 둔다. 기본은 둘 다 켜짐(=지금과 동일하게 항상 보임).
type ExtraFilterKey = "LAG" | "STP_BLOCKED";

function matchesExtraFilter(link: TopologyLink | undefined, hiddenExtras: Set<ExtraFilterKey>): boolean {
  if (!link) return true;
  if (hiddenExtras.has("LAG") && isLagLink(link)) return false;
  if (hiddenExtras.has("STP_BLOCKED") && isStpBlocked(link)) return false;
  return true;
}

// 17.4절: Topology Graph는 network_link를 직접 시각화하며 Core/Distribution/
// Floor/Access/Endpoint 역할과 실제 Port-to-Port 관계를 표시한다. 배치 알고리즘은
// 목적에 따라 자동(force-directed)/원/격자/수평 트리/수직 트리 중 선택할 수 있고,
// 자동 배치 결과가 마음에 들지 않으면 노드를 직접 드래그해 위치를 조정할 수 있다.
//
// [KOS20260922] 이 컴포넌트는 원래 TopologyGraphPage 전체였다. "Topology 화면은
// Device 구분을 구름으로 표시" 요청에 따라 메인 화면은 Role별 구름 개요만 보여주고,
// 이 상세 그래프(배치/필터/검색/클릭 상세까지 포함한 원래 기능 전체)는 구름을
// 클릭했을 때 뜨는 팝업 안에서 재사용하는 형태로 분리했다 - topology를 이제 직접
// fetch하지 않고 부모(TopologyGraphPage)가 이미 조회했거나 Role로 걸러낸 결과를
// prop으로 받는다.
//
// 수백 개 노드를 한 번에 그리면 애니메이션/레이블 렌더링 비용 때문에 팬/줌이 거의
// 반응하지 않을 정도로 느려진다. 17.4절 자체가 "Endpoint가 많으면 유형별로 묶고
// 확대 시 개별 노드로 전환"하도록 명시하므로, 같은 상위 장비 아래 같은 device_type
// Endpoint/Server/Unknown이 임계치를 넘으면 하나의 Cluster 노드로 접어서 표시하고
// 클릭하면 펼친다.
//
// React Flow는 nodes를 그냥 prop으로만 넘기면 드래그가 내부 상태에 반영되지 않고
// 다음 리렌더에서 원래 위치로 되돌아간다 - 드래그를 실제로 반영하려면 공식 패턴대로
// onNodesChange에서 applyNodeChanges로 직접 상태를 갱신해야 한다.
export const ROLE_COLOR: Record<string, string> = {
  CORE_SWITCH: "#dc2626",
  DISTRIBUTION_SWITCH: "#d97706",
  FLOOR_SWITCH: "#2563eb",
  ACCESS_SWITCH: "#0891b2",
  EDGE_DEVICE: "#7c3aed",
  SERVER: "#059669",
  ENDPOINT: "#6b7280",
  UNKNOWN: "#9ca3af",
  CLUSTER: "#334155",
};

const LAYOUT_MODES: LayoutMode[] = ["auto", "circle", "grid", "tree-horizontal", "tree-vertical"];

// 팬/줌 반응성을 지키기 위한 렌더링 원가 절감 임계치: 이 개수를 넘으면 Edge 애니메이션을
// 끈다 (클러스터링 이후 개수 기준이므로, 실제로는 훨씬 큰 원본 그래프에도 적용됨).
// [KOS20260921] 포트 라벨은 "선이 있는데 포트를 모르겠다"는 문제를 눈으로 확인하는
// 핵심 정보라서, 예전처럼 이 임계치를 넘었다고 통째로 숨기지 않는다 - 애니메이션
// (CSS dash-offset 반복)만 무겁고 정적 텍스트 라벨은 비용이 훨씬 낮다.
const HEAVY_GRAPH_NODE_THRESHOLD = 60;

// 17.4절: "Node를 클릭하면 Evidence와 Confidence를 확인할 수 있어야 한다"에 대응.
// 선택한 노드에 연결된 Link만 눈에 띄는 색으로 바꾸고, 나머지는 흐리게 처리한다.
const HIGHLIGHT_EDGE_COLOR = "#facc15";

// [KOS20260921] 링크 상태(DOWN/STALE)가 있으면 그게 항상 우선이고, 정상(UP) 링크는
// depth가 다른 두 장비를 잇는 계층 간 연결(한쪽에선 Uplink, 반대쪽에선 Downlink)인지,
// 같은 depth끼리의 Peer/이중화 연결인지로 구분해 색칠한다.
const HIERARCHICAL_LINK_COLOR = "#3b82f6"; // 상위-하위 계층 연결 (Uplink/Downlink 쌍)
const PEER_LINK_COLOR = "#a78bfa"; // 같은 계층끼리의 Peer/이중화 연결(점선)
// [KOS20260922] "ROOT 노드는 STP에서 찾아서 그리면 좋을 듯하다"는 요청 - STP Root
// Bridge 노드를 다른 노드와 확실히 구분되는 금색 테두리+글로우로 표시한다.
const STP_ROOT_COLOR = "#f59e0b";
// dot1dStpPortState BLOCKING인 포트를 경유하는 링크는 흐리게 회색 점선으로
// 표시해, "물리적으로는 연결돼 있지만 STP가 차단해 실제로는 안 쓰는 경로"임을
// 구분한다.
const STP_BLOCKED_LINK_COLOR = "#9ca3af";

function buildGraph(
  clustered: { nodes: RenderNode[]; links: Topology["links"] },
  mode: LayoutMode,
): { nodes: Node[]; edges: Edge[] } {
  const positions = computeLayoutPositions(clustered as Topology, mode);
  const lightweight = clustered.nodes.length > HEAVY_GRAPH_NODE_THRESHOLD;

  const nodes: Node[] = clustered.nodes.map((n) => {
    const isCluster = n.device_role === "CLUSTER";
    const isStpRoot = n.is_stp_root && !isCluster;
    // [KOS20260922] IP 다음 줄은 SNMP sysName, sysDescription,
    // DEVICE_TYPE 순서로 첫 번째 유효한 값을 표시한다.
    const secondary = (n.sys_name?.trim() || n.sys_descr?.trim() || n.device_type).replace(/\s+/g, " ");
    const primary = n.management_ip || secondary || `#${n.id}`;
    const label = isCluster ? n.hostname || `#${n.id}` : (
      <div title={secondary || undefined}>
        <div>{isStpRoot ? `★ ${primary}` : primary}</div>
        {n.management_ip && secondary && secondary !== n.management_ip && (
          <div style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>({secondary})</div>
        )}
      </div>
    );
    return {
      id: String(n.id),
      position: positions.get(n.id) ?? { x: 0, y: 0 },
      data: { label, node: n },
      style: {
        background: ROLE_COLOR[n.device_role] ?? "#9ca3af",
        color: "white",
        borderRadius: 8,
        padding: 8,
        width: isCluster ? undefined : 160,
        fontSize: 12,
        border: isStpRoot
          ? `4px solid ${STP_ROOT_COLOR}`
          : isCluster
            ? "2px dashed #cbd5e1"
            : n.status === "OFFLINE"
              ? "3px dashed #1f2937"
              : "1px solid rgba(0,0,0,0.15)",
        boxShadow: isStpRoot ? `0 0 0 3px ${STP_ROOT_COLOR}55` : undefined,
        opacity: n.status === "OFFLINE" ? 0.6 : 1,
        cursor: isCluster ? "zoom-in" : "pointer",
      },
    };
  });

  const edges: Edge[] = clustered.links.map((l) => {
    const isPeer = l.link_role === "PEER";
    // [KOS20260921] 색만으로는 구분이 약할 수 있어, 상위-하위 계층 연결
    // (Uplink/Downlink 쌍)은 Peer 연결보다 두껍게 그려 색약/흑백 인쇄에서도
    // 구별되게 한다.
    const baseWidth = Math.max(1, l.confidence / 40);
    const strokeWidth = isPeer ? baseWidth : baseWidth + 1.5;
    // [KOS20260921] "근거(FDB/ARP 등)+Confidence" 대신 실제 포트 번호를 보여
    // 달라는 요청 - 포트 이름을 알 수 있을 때만 "포트no -> 포트no"로 표시하고,
    // 하나도 모르면(대개 ARP-only 근거) 기존처럼 근거+Confidence로 폴백한다.
    const portLabel = l.label?.trim() || (
      l.src_port || l.dst_port
        ? `${formatPortName(l.src_port) ?? "?"} → ${formatPortName(l.dst_port) ?? "?"}`
        : `${l.source} (${l.confidence})`
    );
    const stpBlocked = isStpBlocked(l);
    return {
      id: String(l.id),
      source: String(l.src_device_id),
      target: String(l.dst_device_id),
      type: "bendable",
      label: stpBlocked ? `${portLabel} (STP Blocked)` : portLabel,
      animated: !lightweight && !stpBlocked && l.status === "UP" && l.confidence >= 80,
      data: { link: l },
      style: {
        stroke:
          l.status === "DOWN"
            ? "#dc2626"
            : l.status === "STALE"
              ? "#d97706"
              : stpBlocked
                ? STP_BLOCKED_LINK_COLOR
                : isPeer
                  ? PEER_LINK_COLOR
                  : HIERARCHICAL_LINK_COLOR,
        strokeWidth,
        strokeDasharray: stpBlocked ? "3 3" : l.status === "UP" && isPeer ? "6 4" : undefined,
      },
      markerEnd: { type: MarkerType.ArrowClosed },
    };
  });

  return { nodes, edges };
}

interface TopologyGraphViewProps {
  topology: Topology;
  title?: string;
  // 뷰가 처음 열렸을 때 쓸 배치 모드. 단일 Role만 모아 놓은 팝업에는 상하 계층
  // 구조가 없는 경우가 많아(예: ENDPOINT끼리는 서로 링크가 없음) tree 계열보다
  // grid가 더 알아보기 쉽다.
  defaultLayoutMode?: LayoutMode;
  // [KOS20260922] "ACCESS_SWITCH는 노드를 클릭하면 장비 정보도 보이지만, 팝업으로
  // 하위 노드를 다시 조회해서 그래프로 보여 줘" 요청 - 일반 노드(Cluster 제외)를
  // 클릭했을 때 기존 우측 상세 패널 선택과 별개로 호출된다. 어떤 팝업을 어떻게
  // 열지는 이 뷰를 사용하는 쪽(TopologyGraphPage)이 결정한다.
  onDrillDownNode?: (node: RenderNode) => void;
  onLinkCreated?: (link: TopologyLink) => void;
  onLinkDeleted?: (linkId: number) => void;
  // [KOS20260922] "Topology 팝업에 닫기 버튼을 추가" 요청 - 팝업으로 쓸 때 닫기
  // 버튼을 그래프/필터/범례 전부를 지나 맨 아래에만 두면(이전 구현) 전체 화면
  // 너비 팝업에서는 스크롤해야 눈에 띈다. 제목 옆(항상 스크롤 없이 보이는 위치)에
  // 바로 렌더링할 수 있도록 헤더 우측 요소를 주입받는다.
  headerExtra?: React.ReactNode;
}

export default function TopologyGraphView({
  topology: initialTopology,
  title = "Topology",
  defaultLayoutMode = "tree-vertical",
  onDrillDownNode,
  onLinkCreated,
  onLinkDeleted,
  headerExtra,
}: TopologyGraphViewProps) {
  const [topology, setTopology] = useState<Topology>(initialTopology);
  const [layoutMode, setLayoutMode] = useState<LayoutMode>(defaultLayoutMode);
  const [expandedClusters, setExpandedClusters] = useState<Set<string>>(new Set());
  const [nodes, setNodes] = useState<Node[]>([]);
  const [edges, setEdges] = useState<Edge[]>([]);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [selectedNodeIds, setSelectedNodeIds] = useState<Set<string>>(new Set());
  const [selectedDetail, setSelectedDetail] = useState<DeviceDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null);
  const [portFilter, setPortFilter] = useState<PortFilter>("ALL");
  const [hiddenSources, setHiddenSources] = useState<Set<string>>(new Set());
  const [hiddenExtras, setHiddenExtras] = useState<Set<ExtraFilterKey>>(new Set());
  const [searchQuery, setSearchQuery] = useState("");
  const [searchError, setSearchError] = useState<string | null>(null);
  const [pendingFocusId, setPendingFocusId] = useState<string | null>(null);
  const [modalDeviceId, setModalDeviceId] = useState<number | null>(null);
  const [contextMenu, setContextMenu] = useState<
    { kind: "edge"; edgeId: string; x: number; y: number } | { kind: "node"; nodeId: string; x: number; y: number } | null
  >(null);
  const [linkDraft, setLinkDraft] = useState<{ srcId: number; dstId: number } | null>(null);
  const [linkLabel, setLinkLabel] = useState("");
  const [linkActionError, setLinkActionError] = useState<string | null>(null);
  const [linkActionBusy, setLinkActionBusy] = useState(false);
  const [confirmDeleteLinkId, setConfirmDeleteLinkId] = useState<number | null>(null);
  const [linkPingRunning, setLinkPingRunning] = useState(false);
  const [linkPingResult, setLinkPingResult] = useState<LinkPingResult | null>(null);
  const [linkPingError, setLinkPingError] = useState<string | null>(null);
  const [linkPingProtocol, setLinkPingProtocol] = useState<"SSH" | "TELNET">("SSH");
  const [linkPingDirection, setLinkPingDirection] = useState<"FORWARD" | "REVERSE">("FORWARD");
  const [linkPingUsername, setLinkPingUsername] = useState("admin");
  const [linkPingPassword, setLinkPingPassword] = useState("");
  const [linkPingPort, setLinkPingPort] = useState(22);
  const reactFlowInstanceRef = useRef<ReactFlowInstance | null>(null);

  useEffect(() => {
    setTopology(initialTopology);
  }, [initialTopology]);

  useEffect(() => {
    setLinkPingResult(null);
    setLinkPingError(null);
    setLinkPingPassword("");
    setLinkPingUsername("admin");
    setLinkPingDirection("FORWARD");
  }, [selectedEdgeId]);

  useEffect(() => {
    const link = selectedEdgeId ? topology.links.find((item) => String(item.id) === selectedEdgeId) : null;
    const sourceId = linkPingDirection === "REVERSE" ? link?.dst_device_id : link?.src_device_id;
    const source = sourceId != null ? topology.nodes.find((node) => node.id === sourceId) : null;
    const identity = `${source?.hostname ?? ""} ${source?.sys_name ?? ""} ${source?.device_type ?? ""}`.trim().toUpperCase();
    const protocol = identity.startsWith("NSH") ? "TELNET" : "SSH";
    setLinkPingProtocol(protocol);
    setLinkPingPort(protocol === "TELNET" ? 23 : 22);
  }, [selectedEdgeId, linkPingDirection, topology.links, topology.nodes]);

  // 노드를 선택하면 Device Detail 페이지의 Overview 탭과 동일한 정보를 우측 패널에
  // 보여준다. Topology 노드에는 요약 필드만 있으므로 전체 상세를 별도로 조회한다.
  useEffect(() => {
    if (!selectedNodeId) {
      setSelectedDetail(null);
      return;
    }
    setDetailLoading(true);
    setDetailError(null);
    api
      .getDevice(Number(selectedNodeId))
      .then(setSelectedDetail)
      .catch((err) => setDetailError(err.message))
      .finally(() => setDetailLoading(false));
  }, [selectedNodeId]);

  const clustered = useMemo(
    () => buildClusteredTopology(topology, expandedClusters),
    [topology, expandedClusters],
  );

  // topology/배치/클러스터 펼침 상태가 바뀔 때만 좌표를 다시 계산한다. 이후 사용자가
  // 드래그로 옮긴 위치는 onNodesChange가 관리하는 로컬 state에만 반영되고, 이
  // effect가 다시 돌 때(재조회/배치 전환/클러스터 펼침) 초기화된다 - 각각 "다시
  // 배치하기" 의도이므로 자연스러운 동작이다.
  useEffect(() => {
    const graph = buildGraph(clustered, layoutMode);
    setNodes(graph.nodes);
    setEdges(graph.edges);
    // 선택된 노드가 이번 재계산 결과에도 여전히 존재하면 선택을 유지한다(예: 우측
    // 패널에서 Role을 바꾼 경우). 클러스터로 접혀 사라진 경우에만 선택을 해제한다.
    setSelectedNodeId((current) => (current && graph.nodes.some((n) => n.id === current) ? current : null));
    setSelectedNodeIds((current) => new Set([...current].filter((id) => graph.nodes.some((n) => n.id === id))));
  }, [clustered, layoutMode]);

  const onNodesChange = useCallback((changes: NodeChange[]) => {
    setNodes((current) => applyNodeChanges(changes, current));
  }, []);

  const handleEdgeBendChange = useCallback((edgeId: string, bend: BendPoint) => {
    setEdges((current) =>
      current.map((edge) => (edge.id === edgeId ? { ...edge, data: { ...edge.data, bend } } : edge)),
    );
  }, []);

  // 선택된 노드에 연결된 Link만 눈에 띄는 색으로 강조하고, 그 외에는 흐리게 처리한다
  // (17.4절: Node/Link 클릭 시 Evidence/Confidence 확인 가능해야 한다는 요건의 시각적 대응).
  // Link를 직접 선택했을 때도 같은 강조색을 쓰되, 그 Link 하나만 강조한다.
  const displayEdges = useMemo(() => {
    const filtered = edges.filter(
      (edge) =>
        matchesPortFilter(edge.data?.link, portFilter) &&
        matchesSourceFilter(edge.data?.link, hiddenSources) &&
        matchesExtraFilter(edge.data?.link, hiddenExtras),
    );
    const interactive = filtered.map((edge) => ({
      ...edge,
      selected: edge.id === selectedEdgeId,
      data: { ...edge.data, onBendChange: handleEdgeBendChange },
    }));
    if (!selectedNodeId && !selectedEdgeId) return interactive;
    return interactive.map((edge) => {
      const connected = selectedEdgeId
        ? edge.id === selectedEdgeId
        : edge.source === selectedNodeId || edge.target === selectedNodeId;
      return {
        ...edge,
        animated: connected,
        style: {
          ...edge.style,
          stroke: connected ? HIGHLIGHT_EDGE_COLOR : edge.style?.stroke,
          strokeWidth: connected ? 3 : edge.style?.strokeWidth,
          opacity: connected ? 1 : 0.2,
        },
        zIndex: connected ? 10 : 0,
      };
    });
  }, [edges, selectedNodeId, selectedEdgeId, portFilter, hiddenSources, hiddenExtras, handleEdgeBendChange]);

  // 근거(Source) 필터 체크박스 목록 - 하드코딩하지 않고 실제 데이터에 있는 값만 보여준다.
  // CLUSTER는 서버 응답(topology.links)에는 없고 클러스터링 단계에서 클라이언트가
  // 만들어내는 요약선이라 별도로 덧붙인다 - 실제로 클러스터가 하나라도 있을 때만.
  const availableSources = useMemo(() => {
    const real = Array.from(new Set(topology.links.map((l) => l.source))).sort();
    const hasCluster = clustered.nodes.some((n) => n.device_role === "CLUSTER");
    return hasCluster ? [...real, "CLUSTER"] : real;
  }, [topology, clustered]);

  const toggleSource = (source: string) => {
    setHiddenSources((prev) => {
      const next = new Set(prev);
      if (next.has(source)) next.delete(source);
      else next.add(source);
      return next;
    });
  };

  const toggleExtra = (key: ExtraFilterKey) => {
    setHiddenExtras((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  // [KOS20260922] "전체 선택/해제 체크 박스 근거 표시 맨 앞에 추가" 요청 - 근거
  // 표시 줄의 모든 체크박스(availableSources + LAG + STP 차단)를 한 번에
  // 켜거나 끈다. 일부만 꺼져 있으면 indeterminate로 표시해 "전부 켜짐"과
  // 헷갈리지 않게 한다.
  const allSourcesVisible = hiddenSources.size === 0 && hiddenExtras.size === 0;
  const allSourcesHidden = hiddenSources.size >= availableSources.length && hiddenExtras.size >= 2;
  const selectAllRef = useRef<HTMLInputElement | null>(null);
  useEffect(() => {
    if (selectAllRef.current) {
      selectAllRef.current.indeterminate = !allSourcesVisible && !allSourcesHidden;
    }
  }, [allSourcesVisible, allSourcesHidden]);

  const toggleAllSources = () => {
    if (allSourcesVisible) {
      setHiddenSources(new Set(availableSources));
      setHiddenExtras(new Set(["LAG", "STP_BLOCKED"]));
    } else {
      setHiddenSources(new Set());
      setHiddenExtras(new Set());
    }
  };

  const displayNodes = useMemo(() => {
    if (!selectedNodeId && !selectedEdgeId && selectedNodeIds.size === 0) return nodes;
    const connectedIds = new Set<string>();
    if (selectedEdgeId) {
      const edge = edges.find((e) => e.id === selectedEdgeId);
      if (edge) {
        connectedIds.add(edge.source);
        connectedIds.add(edge.target);
      }
    } else if (selectedNodeId) {
      connectedIds.add(selectedNodeId);
      for (const edge of edges) {
        if (edge.source === selectedNodeId) connectedIds.add(edge.target);
        if (edge.target === selectedNodeId) connectedIds.add(edge.source);
      }
    }
    return nodes.map((node) => {
      const isSelected = selectedNodeIds.has(node.id) || node.id === selectedNodeId || connectedIds.has(node.id) && selectedEdgeId != null;
      const isConnected = connectedIds.has(node.id);
      return {
        ...node,
        style: {
          ...node.style,
          opacity: isConnected || selectedNodeIds.has(node.id) ? 1 : 0.35,
          boxShadow: isSelected ? `0 0 0 3px ${HIGHLIGHT_EDGE_COLOR}` : undefined,
        },
      };
    });
  }, [nodes, edges, selectedNodeId, selectedNodeIds, selectedEdgeId]);

  const handleRoleChange = async (role: string) => {
    if (!selectedDetail) return;
    try {
      const updated = await api.updateRole(selectedDetail.id, role);
      setSelectedDetail({ ...selectedDetail, ...updated });
      // 그래프 노드 색상도 즉시 반영되도록 로컬 topology state의 역할을 함께 갱신한다.
      setTopology((prev) => ({ ...prev, nodes: prev.nodes.map((n) => (n.id === updated.id ? { ...n, device_role: updated.device_role } : n)) }));
    } catch (err: any) {
      setDetailError(err.message);
    }
  };

  const handleNodeClick = (_: unknown, node: Node) => {
    const renderNode: RenderNode = node.data.node;
    if (renderNode.device_role === "CLUSTER" && renderNode.cluster_key) {
      setExpandedClusters((prev) => new Set(prev).add(renderNode.cluster_key!));
      return;
    }
    setSelectedEdgeId(null);
    setSelectedNodeIds((current) => {
      const next = new Set(current);
      if (next.has(node.id)) next.delete(node.id);
      else {
        if (next.size >= 2) next.clear();
        next.add(node.id);
      }
      return next;
    });
    setSelectedNodeId(node.id);
    onDrillDownNode?.(renderNode);
  };

  const handleEdgeClick = (_: unknown, edge: Edge) => {
    setSelectedNodeId(null);
    setSelectedNodeIds(new Set());
    setSelectedEdgeId((current) => (current === edge.id ? null : edge.id));
  };

  const handlePaneClick = () => {
    setSelectedNodeId(null);
    setSelectedNodeIds(new Set());
    setSelectedEdgeId(null);
    setContextMenu(null);
  };

  const handleNodeContextMenu = (event: React.MouseEvent, node: Node) => {
    event.preventDefault();
    if (selectedNodeIds.size !== 2 || !selectedNodeIds.has(node.id)) return;
    setContextMenu({ kind: "node", nodeId: node.id, x: event.clientX, y: event.clientY });
  };

  const handleEdgeContextMenu = (event: React.MouseEvent, edge: Edge) => {
    event.preventDefault();
    const link: TopologyLink | undefined = edge.data?.link;
    if (!link || link.id < 0 || link.source === "CLUSTER") return;
    setSelectedNodeId(null);
    setSelectedNodeIds(new Set());
    setSelectedEdgeId(edge.id);
    setContextMenu({ kind: "edge", edgeId: edge.id, x: event.clientX, y: event.clientY });
  };

  const openCreateLinkDialog = () => {
    const ids = [...selectedNodeIds].map(Number);
    if (ids.length !== 2) return;
    setLinkLabel("");
    setLinkActionError(null);
    setLinkDraft({ srcId: ids[0], dstId: ids[1] });
  };

  const handleCreateLink = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!linkDraft || !linkLabel.trim()) return;
    setLinkActionBusy(true);
    setLinkActionError(null);
    try {
      const link = await api.createTopologyLink({
        src_device_id: linkDraft.srcId,
        dst_device_id: linkDraft.dstId,
        label: linkLabel.trim(),
      });
      setTopology((current) => ({ ...current, links: [...current.links.filter((item) => item.id !== link.id), link] }));
      onLinkCreated?.(link);
      setSelectedNodeIds(new Set());
      setSelectedNodeId(null);
      setSelectedEdgeId(String(link.id));
      setLinkDraft(null);
    } catch (err: any) {
      setLinkActionError(err.message);
    } finally {
      setLinkActionBusy(false);
    }
  };

  const handleDeleteLink = async () => {
    if (confirmDeleteLinkId == null) return;
    const linkId = confirmDeleteLinkId;
    setLinkActionBusy(true);
    setLinkActionError(null);
    try {
      await api.deleteTopologyLink(linkId);
      setTopology((current) => ({ ...current, links: current.links.filter((link) => link.id !== linkId) }));
      onLinkDeleted?.(linkId);
      setSelectedEdgeId(null);
      setConfirmDeleteLinkId(null);
    } catch (err: any) {
      setLinkActionError(err.message);
    } finally {
      setLinkActionBusy(false);
    }
  };

  const requestDeleteLink = (linkId: number) => {
    setLinkActionError(null);
    setConfirmDeleteLinkId(linkId);
  };

  const handleLinkPing = async (linkId: number) => {
    if (linkPingPassword && !linkPingUsername.trim()) {
      setLinkPingError("CLI ID를 입력해 주세요.");
      return;
    }
    setLinkPingRunning(true);
    setLinkPingResult(null);
    setLinkPingError(null);
    try {
      const credential = linkPingPassword ? {
        direction: linkPingDirection,
        protocol: linkPingProtocol,
        username: linkPingUsername.trim(),
        password: linkPingPassword,
        port: linkPingPort,
      } : {
        direction: linkPingDirection,
        protocol: linkPingProtocol,
        username: linkPingUsername.trim() || undefined,
        port: linkPingPort,
      };
      setLinkPingResult(await api.pingTopologyLink(linkId, credential));
    } catch (err: any) {
      setLinkPingError(err.message);
    } finally {
      setLinkPingRunning(false);
    }
  };

  const nodeLabel = (id: string | number): string => {
    const found = topology.nodes.find((n) => String(n.id) === String(id));
    return found ? found.hostname || found.management_ip || `#${found.id}` : `#${id}`;
  };

  const nodeDepth = (id: string | number): number => {
    const found = topology.nodes.find((n) => String(n.id) === String(id));
    return found ? found.discovery_depth : 0;
  };

  const selectedLink = selectedEdgeId ? edges.find((e) => e.id === selectedEdgeId)?.data?.link : null;
  const linkPingSourceId = selectedLink
    ? (linkPingDirection === "REVERSE" ? selectedLink.dst_device_id : selectedLink.src_device_id)
    : null;
  const linkPingSource = linkPingSourceId != null
    ? topology.nodes.find((node) => node.id === linkPingSourceId)
    : null;
  const linkPingSourceIdentity = `${linkPingSource?.hostname ?? ""} ${linkPingSource?.sys_name ?? ""} ${linkPingSource?.device_type ?? ""}`
    .trim()
    .toUpperCase();
  const linkPingDefaultName = linkPingSourceIdentity.startsWith("NSH")
    ? "NSH 기본 Credential"
    : linkPingSourceIdentity.startsWith("NHM")
      ? "NHM 기본 Credential"
      : "장비 Credential Profile";

  // [KOS20260921] 링크가 많으면(예: 저신뢰 ARP-only 링크 수십 개가 한 장비에
  // 몰린 경우) 그래프만으로는 "상위→하위로 어떻게 이어지는지" 눈으로 따라가기
  // 어렵다는 사용자 지적에 따라, 선택한 노드(또는 선택한 링크의 하위 쪽)에서
  // depth가 더 작은(상위) 이웃을 계속 따라가며 Core까지의 경로를 문자열 체인으로
  // 보여준다. 여러 상위 후보가 있으면 depth가 가장 작은 쪽(가장 상위)을 우선한다.
  const upstreamPath = useMemo((): string[] => {
    const anchorId = selectedNodeId ?? (selectedLink ? String(selectedLink.dst_device_id) : null);
    if (!anchorId) return [];
    const path: string[] = [anchorId];
    const visited = new Set<string>([anchorId]);
    let currentId = anchorId;
    for (let i = 0; i < 30; i++) {
      const currentDepth = nodeDepth(currentId);
      if (currentDepth <= 0) break;
      let bestParent: string | null = null;
      let bestDepth = currentDepth;
      for (const link of topology.links) {
        const srcId = String(link.src_device_id);
        const dstId = String(link.dst_device_id);
        const otherId = srcId === currentId ? dstId : dstId === currentId ? srcId : null;
        if (!otherId || visited.has(otherId)) continue;
        const otherDepth = nodeDepth(otherId);
        if (otherDepth < bestDepth) {
          bestDepth = otherDepth;
          bestParent = otherId;
        }
      }
      if (!bestParent) break;
      path.push(bestParent);
      visited.add(bestParent);
      currentId = bestParent;
    }
    return path.reverse().map((id) => nodeLabel(id)); // 최상위(Core) -> ... -> 선택 노드 순서
  }, [selectedNodeId, selectedLink, topology]);

  const expandAll = () => {
    const { clusterKeys } = buildClusteredTopology(topology, new Set());
    setExpandedClusters(new Set(clusterKeys));
  };

  const collapseAll = () => setExpandedClusters(new Set());

  // [KOS20260921] IP/MAC으로 노드를 찾아 화면을 이동시켜 달라는 요청 - 검색된
  // 장비가 Cluster에 접혀 있으면 먼저 모두 펼치고(expandAll), 펼쳐진 결과에
  // 실제로 그 노드가 나타나면(다음 렌더의 nodes) 선택 + 화면 중심 이동을 한다.
  const handleSearch = () => {
    const q = searchQuery.trim().toLowerCase();
    setSearchError(null);
    if (!q) return;
    const match = topology.nodes.find(
      (n) => (n.management_ip && n.management_ip.toLowerCase().includes(q)) || (n.primary_mac && n.primary_mac.toLowerCase().includes(q)),
    );
    if (!match) {
      setSearchError("IP 또는 MAC이 일치하는 장비를 찾을 수 없습니다.");
      return;
    }
    expandAll();
    setPendingFocusId(String(match.id));
  };

  useEffect(() => {
    if (!pendingFocusId) return;
    const node = nodes.find((n) => n.id === pendingFocusId);
    if (!node) return; // 클러스터 펼침이 아직 반영되지 않음 - 다음 nodes 갱신을 기다린다.
    setSelectedEdgeId(null);
    setSelectedNodeId(pendingFocusId);
    setSelectedNodeIds(new Set([pendingFocusId]));
    reactFlowInstanceRef.current?.setCenter(node.position.x + 75, node.position.y + 20, { zoom: 1.5, duration: 500 });
    setPendingFocusId(null);
  }, [nodes, pendingFocusId]);

  const clusterCount = clustered.nodes.filter((n) => n.device_role === "CLUSTER").length;

  return (
    <div>
      <div className="page-header">
        <h1>{title}</h1>
        <span className="muted">
          {`${topology.nodes.length} nodes · ${topology.links.length} links`}
          {clusterCount > 0 && ` · ${clusterCount}개 클러스터로 축약됨`}
        </span>
        {headerExtra}
      </div>

      <div className="toolbar">
        <label htmlFor="layout-select" className="muted" style={{ fontSize: 13 }}>
          배치:
        </label>
        <select id="layout-select" value={layoutMode} onChange={(e) => setLayoutMode(e.target.value as LayoutMode)}>
          {LAYOUT_MODES.map((mode) => (
            <option key={mode} value={mode}>
              {LAYOUT_LABELS[mode]}
            </option>
          ))}
        </select>
        <button className="btn" onClick={expandAll} disabled={clusterCount === 0 && expandedClusters.size === 0}>
          모두 펼치기
        </button>
        <button className="btn" onClick={collapseAll} disabled={expandedClusters.size === 0}>
          모두 접기
        </button>
        <label htmlFor="port-filter-select" className="muted" style={{ fontSize: 13, marginLeft: 8 }}>
          포트 표시:
        </label>
        <select id="port-filter-select" value={portFilter} onChange={(e) => setPortFilter(e.target.value as PortFilter)}>
          <option value="ALL">전체 링크</option>
          <option value="BOTH_KNOWN">양쪽 포트 모두 확인된 링크만</option>
          <option value="PARTIAL">한쪽 이상 포트 미확인 링크만</option>
        </select>
        <input
          type="search"
          placeholder="IP 또는 MAC 검색"
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") handleSearch();
          }}
          style={{ width: 160 }}
        />
        <button className="btn" onClick={handleSearch}>
          검색
        </button>
        <span className="muted" style={{ fontSize: 12, marginLeft: 8 }}>
          (노드 2개 좌클릭 후 링크 추가 · 우클릭 드래그: 영역 확대 · Shift+우클릭 드래그: 영역 축소 · 링크 좌클릭 드래그: 굴곡 조정)
        </span>
      </div>
      {searchError && <div className="error-banner">{searchError}</div>}

      {availableSources.length > 0 && (
        <div className="toolbar">
          <span className="muted" style={{ fontSize: 13 }}>
            근거 표시:
          </span>
          <label style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 13 }} title="아래 모든 근거를 한 번에 켜거나 끕니다">
            <input ref={selectAllRef} type="checkbox" checked={allSourcesVisible} onChange={toggleAllSources} />
            전체
          </label>
          {availableSources.map((source) => (
            <label key={source} style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 13 }}>
              <input type="checkbox" checked={!hiddenSources.has(source)} onChange={() => toggleSource(source)} />
              {source}
            </label>
          ))}
          <label style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 13 }} title="LAG(Link Aggregation) 포트를 경유하는 링크">
            <input type="checkbox" checked={!hiddenExtras.has("LAG")} onChange={() => toggleExtra("LAG")} />
            LAG
          </label>
          <label
            style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 13 }}
            title="STP가 Blocking 처리해 논리적으로 막아둔 링크"
          >
            <input type="checkbox" checked={!hiddenExtras.has("STP_BLOCKED")} onChange={() => toggleExtra("STP_BLOCKED")} />
            STP 차단
          </label>
        </div>
      )}

      <div style={{ display: "flex", gap: 16, alignItems: "flex-start" }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <TopologyBoxZoom
            flowInstanceRef={reactFlowInstanceRef}
            className="card"
            style={{ height: "70vh", width: "100%", padding: 0, overflow: "hidden" }}
          >
            <ReactFlow
              nodes={displayNodes}
              edges={displayEdges}
              edgeTypes={EDGE_TYPES}
              onInit={(instance) => {
                reactFlowInstanceRef.current = instance;
              }}
              onNodesChange={onNodesChange}
              nodesDraggable
              panOnDrag
              panOnScroll={false}
              zoomOnScroll
              zoomOnPinch
              selectionOnDrag={false}
              onNodeClick={handleNodeClick}
              onNodeContextMenu={handleNodeContextMenu}
              onEdgeClick={handleEdgeClick}
              onEdgeContextMenu={handleEdgeContextMenu}
              onPaneClick={handlePaneClick}
              fitView
            >
              <Background />
              <Controls showInteractive={false} />
            </ReactFlow>
          </TopologyBoxZoom>

          <div className="card">
            <div className="tree-group-title">범례 (Role)</div>
            <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
              {Object.entries(ROLE_COLOR).map(([role, color]) => (
                <span key={role} style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12 }}>
                  <span style={{ width: 12, height: 12, borderRadius: 4, background: color, display: "inline-block" }} />
                  {role === "CLUSTER" ? "CLUSTER (점선, 클릭 시 펼침)" : role}
                </span>
              ))}
              <span style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12 }}>
                <span
                  style={{
                    width: 12,
                    height: 12,
                    borderRadius: 4,
                    background: "#9ca3af",
                    border: `3px solid ${STP_ROOT_COLOR}`,
                    display: "inline-block",
                  }}
                />
                ★ STP ROOT (Root Bridge)
              </span>
            </div>
          </div>

          <div className="card">
            <div className="tree-group-title">범례 (Link)</div>
            <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
              <span style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12 }}>
                <span style={{ width: 20, height: 3, background: HIERARCHICAL_LINK_COLOR, display: "inline-block" }} />
                상위-하위 계층 연결 (한쪽은 Uplink · 반대쪽은 Downlink, 굵게 표시)
              </span>
              <span style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12 }}>
                <span
                  style={{
                    width: 20,
                    height: 2,
                    background: `repeating-linear-gradient(90deg, ${PEER_LINK_COLOR} 0 6px, transparent 6px 10px)`,
                    display: "inline-block",
                  }}
                />
                동일 계층 Peer/이중화 연결
              </span>
              <span style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12 }}>
                <span style={{ width: 20, height: 3, background: "#d97706", display: "inline-block" }} />
                STALE
              </span>
              <span style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12 }}>
                <span style={{ width: 20, height: 3, background: "#dc2626", display: "inline-block" }} />
                DOWN
              </span>
              <span style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12 }}>
                <span
                  style={{
                    width: 20,
                    height: 2,
                    background: `repeating-linear-gradient(90deg, ${STP_BLOCKED_LINK_COLOR} 0 3px, transparent 3px 6px)`,
                    display: "inline-block",
                  }}
                />
                STP Blocked (물리적으로 연결됐지만 STP가 차단)
              </span>
            </div>
          </div>
        </div>

        {selectedNodeId && (
          <div className="card" style={{ width: 380, flexShrink: 0, maxHeight: "70vh", overflowY: "auto" }}>
            <div className="page-header">
              <h1 style={{ fontSize: 16 }}>장비 정보</h1>
              <div style={{ display: "flex", gap: 8 }}>
                <button
                  className="btn btn-primary"
                  disabled={selectedNodeIds.size !== 2}
                  title={selectedNodeIds.size === 2 ? "선택한 두 장비 사이에 링크 추가" : "노드를 두 개 선택해 주세요"}
                  onClick={openCreateLinkDialog}
                >
                  링크 추가
                </button>
                <button className="btn" onClick={() => { setSelectedNodeId(null); setSelectedNodeIds(new Set()); }}>
                  닫기 ✕
                </button>
              </div>
            </div>
            <div className="muted" style={{ fontSize: 12, marginBottom: 10 }}>
              {selectedNodeIds.size === 2
                ? `링크 대상: ${[...selectedNodeIds].map(nodeLabel).join(" ↔ ")}`
                : "링크를 추가하려면 그래프에서 연결할 노드를 하나 더 클릭하세요."}
            </div>
            {upstreamPath.length > 1 && (
              <div className="muted" style={{ fontSize: 12, marginBottom: 10, lineHeight: 1.6 }}>
                상위 경로(Core 방향): {upstreamPath.join(" → ")}
              </div>
            )}
            {detailLoading && <div className="muted">불러오는 중...</div>}
            {detailError && <div className="error-banner">{detailError}</div>}
            {selectedDetail && !detailLoading && (
              <>
                <DeviceOverviewPanel device={selectedDetail} onRoleChange={handleRoleChange} />
                <button
                  className="btn btn-primary"
                  style={{ marginTop: 12, width: "100%" }}
                  onClick={() => setModalDeviceId(selectedDetail.id)}
                >
                  전체 상세 페이지 열기 (Interfaces/ARP/Routes 등) →
                </button>
              </>
            )}
          </div>
        )}

        {selectedLink && (
          <div className="card" style={{ width: 380, flexShrink: 0, maxHeight: "70vh", overflowY: "auto" }}>
            <div className="page-header">
              <h1 style={{ fontSize: 16 }}>링크 정보</h1>
              <div style={{ display: "flex", gap: 8 }}>
                {selectedLink.id >= 0 && selectedLink.source !== "CLUSTER" && (
                  <button className="btn btn-danger" style={{ background: "#dc2626", color: "white" }} onClick={() => requestDeleteLink(selectedLink.id)}>
                    삭제
                  </button>
                )}
                <button className="btn" onClick={() => setSelectedEdgeId(null)}>
                  닫기 ✕
                </button>
              </div>
            </div>
            {selectedLink.link_role === "HIERARCHICAL" ? (() => {
              const srcIsParent = nodeDepth(selectedLink.src_device_id) < nodeDepth(selectedLink.dst_device_id);
              const parentId = srcIsParent ? selectedLink.src_device_id : selectedLink.dst_device_id;
              const childId = srcIsParent ? selectedLink.dst_device_id : selectedLink.src_device_id;
              return (
                <div className="muted" style={{ fontSize: 13, marginBottom: 10, lineHeight: 1.6 }}>
                  <strong style={{ color: "inherit" }}>{nodeLabel(parentId)}</strong> (상위/Parent) → <strong style={{ color: "inherit" }}>{nodeLabel(childId)}</strong> (하위/Child)
                </div>
              );
            })() : (
              <div className="muted" style={{ fontSize: 13, marginBottom: 10 }}>
                {nodeLabel(selectedLink.src_device_id)} ↔ {nodeLabel(selectedLink.dst_device_id)} (동일 계층 Peer)
              </div>
            )}
            {/* [KOS20260921] 링크는 두 장비를 연결하므로 "상세 보기" 버튼도 두 개
                (양쪽 Endpoint) 필요하다 - 페이지 이동 대신 팝업으로 띄워 Topology
                그래프 컨텍스트(선택/배치)를 잃지 않게 한다. */}
            <div style={{ display: "flex", gap: 8, marginBottom: 10 }}>
              <button
                className="btn"
                style={{ flex: 1 }}
                onClick={() => setModalDeviceId(selectedLink.src_device_id)}
              >
                {nodeLabel(selectedLink.src_device_id)} 상세 보기 →
              </button>
              <button
                className="btn"
                style={{ flex: 1 }}
                onClick={() => setModalDeviceId(selectedLink.dst_device_id)}
              >
                {nodeLabel(selectedLink.dst_device_id)} 상세 보기 →
              </button>
            </div>
            {upstreamPath.length > 1 && (
              <div className="muted" style={{ fontSize: 12, marginBottom: 10, lineHeight: 1.6 }}>
                상위 경로(Core 방향): {upstreamPath.join(" → ")}
              </div>
            )}
            <table>
              <tbody>
                <tr>
                  <th>관계</th>
                  <td>
                    {selectedLink.link_role === "PEER"
                      ? "동일 계층 Peer/이중화 연결"
                      : "상위-하위 계층 연결 (상위 장비 관점에선 Downlink · 하위 장비 관점에선 Uplink)"}
                  </td>
                </tr>
                <tr>
                  <th>상태</th>
                  <td>
                    <StatusBadge status={selectedLink.status} />
                  </td>
                </tr>
                <tr>
                  <th>라벨</th>
                  <td>{selectedLink.label ?? "-"}</td>
                </tr>
                <tr>
                  <th>근거(Source)</th>
                  <td>{selectedLink.source}</td>
                </tr>
                <tr>
                  <th>Confidence</th>
                  <td>{selectedLink.confidence}</td>
                </tr>
                <tr>
                  <th>Src Port</th>
                  <td>{formatPortName(selectedLink.src_port) ?? (selectedLink.src_interface_id ? `#${selectedLink.src_interface_id}` : "-")}</td>
                </tr>
                <tr>
                  <th>Dst Port</th>
                  <td>{formatPortName(selectedLink.dst_port) ?? (selectedLink.dst_interface_id ? `#${selectedLink.dst_interface_id}` : "-")}</td>
                </tr>
                <tr>
                  <th>최초 발견</th>
                  <td className="muted">{formatUtcDateTime(selectedLink.first_seen_at)}</td>
                </tr>
                <tr>
                  <th>최근 확인</th>
                  <td className="muted">{formatUtcDateTime(selectedLink.last_seen_at)}</td>
                </tr>
              </tbody>
            </table>
            {selectedLink.id >= 0 && selectedLink.source !== "CLUSTER" && (
              <div style={{ marginTop: 16, paddingTop: 14, borderTop: "1px solid var(--border)" }}>
                <div className="tree-group-title">From → To Ping 연결 확인</div>
                <select
                  aria-label="Ping 실행 방향"
                  value={linkPingDirection}
                  onChange={(event) => setLinkPingDirection(event.target.value as "FORWARD" | "REVERSE")}
                  style={{ width: "100%", marginBottom: 8 }}
                >
                  <option value="FORWARD">
                    {nodeLabel(selectedLink.src_device_id)} → {nodeLabel(selectedLink.dst_device_id)}
                  </option>
                  <option value="REVERSE">
                    {nodeLabel(selectedLink.dst_device_id)} → {nodeLabel(selectedLink.src_device_id)}
                  </option>
                </select>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 90px", gap: 8, marginBottom: 8 }}>
                  <select
                    aria-label="CLI 접속 프로토콜"
                    value={linkPingProtocol}
                    onChange={(event) => {
                      const protocol = event.target.value as "SSH" | "TELNET";
                      setLinkPingProtocol(protocol);
                      setLinkPingPort(protocol === "TELNET" ? 23 : 22);
                    }}
                  >
                    <option value="SSH">SSH</option>
                    <option value="TELNET">Telnet</option>
                  </select>
                  <input
                    type="number"
                    min={1}
                    max={65535}
                    aria-label="CLI 접속 포트"
                    title="접속 포트"
                    value={linkPingPort}
                    onChange={(event) => setLinkPingPort(Number(event.target.value))}
                  />
                </div>
                <input
                  type="text"
                  aria-label="CLI ID"
                  placeholder="ID"
                  value={linkPingUsername}
                  onChange={(event) => setLinkPingUsername(event.target.value)}
                  style={{ width: "100%", marginBottom: 8 }}
                />
                <input
                  type="password"
                  aria-label="CLI Password"
                  placeholder="Password (비우면 장비별 기본값 사용)"
                  value={linkPingPassword}
                  onChange={(event) => setLinkPingPassword(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") handleLinkPing(selectedLink.id);
                  }}
                  style={{ width: "100%", marginBottom: 8 }}
                />
                {!linkPingPassword && (
                  <div className="muted" style={{ fontSize: 12, marginBottom: 8 }}>
                    Password가 비어 있어 {linkPingDefaultName}을 사용합니다.
                  </div>
                )}
                <button
                  className="btn btn-primary"
                  style={{ width: "100%" }}
                  disabled={linkPingRunning}
                  onClick={() => handleLinkPing(selectedLink.id)}
                >
                  {linkPingRunning ? "From → To Ping 실행 중..." : "From → To Ping으로 연결 확인"}
                </button>
                {linkPingError && <div className="error-banner" style={{ marginTop: 8 }}>{linkPingError}</div>}
                {linkPingResult && (
                  <div style={{ marginTop: 10 }}>
                    <div style={{ fontWeight: 700, color: linkPingResult.success ? "#059669" : "#dc2626" }}>
                      {linkPingResult.from_ip} → {linkPingResult.to_ip}: {linkPingResult.success ? "연결 성공" : "연결 실패"}
                    </div>
                    <div className="muted" style={{ marginTop: 4 }}>접속 방식: {linkPingResult.protocol ?? linkPingProtocol}</div>
                    {!linkPingResult.supported && <div className="muted" style={{ marginTop: 4 }}>원격 Ping을 실행할 수 없습니다.</div>}
                    {linkPingResult.packet_loss_percent != null && (
                      <div className="muted" style={{ marginTop: 4 }}>패킷 손실률: {linkPingResult.packet_loss_percent}%</div>
                    )}
                    {linkPingResult.rtt_avg_ms != null && (
                      <div className="muted">
                        RTT 최소/평균/최대: {linkPingResult.rtt_min_ms} / {linkPingResult.rtt_avg_ms} / {linkPingResult.rtt_max_ms} ms
                      </div>
                    )}
                    {linkPingResult.command && <div className="muted" style={{ marginTop: 4 }}>명령: {linkPingResult.command}</div>}
                    <pre className="diagnostic-output" style={{ marginTop: 8 }}>{linkPingResult.output}</pre>
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </div>

      {contextMenu?.kind === "edge" && (
        <TopologyContextMenu
          x={contextMenu.x}
          y={contextMenu.y}
          items={[{
            label: "링크 삭제",
            danger: true,
            onClick: () => requestDeleteLink(Number(contextMenu.edgeId)),
          }]}
          onClose={() => setContextMenu(null)}
        />
      )}
      {contextMenu?.kind === "node" && (
        <TopologyContextMenu
          x={contextMenu.x}
          y={contextMenu.y}
          items={[{ label: "링크 추가", onClick: openCreateLinkDialog }]}
          onClose={() => setContextMenu(null)}
        />
      )}

      {linkDraft && (
        <Modal onClose={() => !linkActionBusy && setLinkDraft(null)} width={440}>
          <form onSubmit={handleCreateLink}>
            <div className="page-header">
              <h1 style={{ fontSize: 18 }}>링크 추가</h1>
              <button type="button" className="btn" disabled={linkActionBusy} onClick={() => setLinkDraft(null)}>
                닫기 ✕
              </button>
            </div>
            <div className="muted" style={{ marginBottom: 12 }}>
              {nodeLabel(linkDraft.srcId)} ↔ {nodeLabel(linkDraft.dstId)}
            </div>
            <label style={{ display: "block", fontSize: 13, marginBottom: 6 }} htmlFor="manual-link-label">
              라벨
            </label>
            <input
              id="manual-link-label"
              autoFocus
              maxLength={255}
              value={linkLabel}
              onChange={(event) => setLinkLabel(event.target.value)}
              style={{ width: "100%" }}
            />
            {linkActionError && <div className="error-banner" style={{ marginTop: 10 }}>{linkActionError}</div>}
            <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 16 }}>
              <button type="button" className="btn" disabled={linkActionBusy} onClick={() => setLinkDraft(null)}>취소</button>
              <button type="submit" className="btn btn-primary" disabled={linkActionBusy || !linkLabel.trim()}>
                {linkActionBusy ? "저장 중..." : "저장"}
              </button>
            </div>
          </form>
        </Modal>
      )}

      {confirmDeleteLinkId != null && (
        <Modal onClose={() => !linkActionBusy && setConfirmDeleteLinkId(null)} width={400}>
          <h1 style={{ fontSize: 18 }}>링크 삭제</h1>
          <p>삭제하시겠습니까?</p>
          {linkActionError && <div className="error-banner">{linkActionError}</div>}
          <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 16 }}>
            <button className="btn" disabled={linkActionBusy} onClick={() => setConfirmDeleteLinkId(null)}>아니오</button>
            <button className="btn btn-danger" style={{ background: "#dc2626", color: "white" }} disabled={linkActionBusy} onClick={handleDeleteLink}>
              {linkActionBusy ? "삭제 중..." : "예"}
            </button>
          </div>
        </Modal>
      )}

      {modalDeviceId != null && (
        <Modal onClose={() => setModalDeviceId(null)} width={1000}>
          <DeviceDetailContent
            deviceId={modalDeviceId}
            headerExtra={
              <button className="btn" onClick={() => setModalDeviceId(null)}>
                닫기 ✕
              </button>
            }
          />
        </Modal>
      )}
    </div>
  );
}
