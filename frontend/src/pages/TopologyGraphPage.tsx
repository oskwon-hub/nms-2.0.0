import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ReactFlow, {
  applyNodeChanges,
  Background,
  Controls,
  Edge,
  EdgeTypes,
  MarkerType,
  Node,
  NodeChange,
  NodeTypes,
  ReactFlowInstance,
} from "reactflow";
import "reactflow/dist/style.css";
import { api, Topology, TopologyLink, TopologyNode } from "../api/client";
import {
  buildRoleCloudGraph,
  filterTopologyByDeviceChildren,
  filterTopologyByDeviceDescendants,
  filterTopologyByRole,
  RoleCloudEdge,
  RoleCloudNode as RoleCloudSummary,
} from "../lib/topologyRoleClouds";
import { computeLayoutPositions, LAYOUT_LABELS, LayoutMode, NodePosition } from "../lib/topologyLayouts";
import RoleCloudNode, { RoleCloudNodeData } from "../components/RoleCloudNode";
import TopologyGraphView, { ROLE_COLOR } from "../components/TopologyGraphView";
import { RenderNode } from "../lib/topologyClustering";
import Modal from "../components/Modal";
import TopologyBoxZoom from "../components/TopologyBoxZoom";
import BendableEdge, { BendPoint } from "../components/BendableEdge";

// nodeTypes 객체를 매 렌더마다 새로 만들면 React Flow가 경고를 띄우고 불필요하게
// 다시 그리므로 컴포넌트 바깥에서 한 번만 만든다.
const NODE_TYPES: NodeTypes = { cloud: RoleCloudNode };
const EDGE_TYPES: EdgeTypes = { bendable: BendableEdge };

// [KOS20260922] "Topology 화면에 배치 옵션이 없는데 자동/원/격자/수평 트리/수직
// 트리 배치를 추가해 줘" 요청 - TopologyGraphView(팝업)에 이미 있는 것과 동일한
// 5가지 배치를 메인 구름 개요에도 추가한다. computeLayoutPositions()는
// TopologyNode/TopologyLink 모양을 기대하므로, Role 구름을 그 모양의 "가짜"
// 노드/링크로 바꿔 계산한 뒤 결과 좌표를 Role 이름으로 다시 매핑한다.
const LAYOUT_MODES: LayoutMode[] = ["auto", "circle", "grid", "tree-horizontal", "tree-vertical"];

// Role 구름(160x100)이 겹치지 않는 최소 간격은 유지하되, 링크가 지나치게 길어지지
// 않도록 추가 좌표 확대는 적용하지 않는다.
const CLOUD_AUTO_LAYOUT_RADIUS = 110;
const CLOUD_LAYOUT_SPACING_SCALE = 1;

function computeCloudLayoutPositions(
  roleSummaries: RoleCloudSummary[],
  roleEdges: RoleCloudEdge[],
  mode: LayoutMode,
): Map<string, NodePosition> {
  const indexByRole = new Map(roleSummaries.map((r, i) => [r.role, i]));
  const fakeNodes: TopologyNode[] = roleSummaries.map((r, i) => ({
    id: i,
    hostname: r.role,
    sys_name: null,
    management_ip: null,
    sys_descr: null,
    primary_mac: null,
    device_type: r.role,
    device_role: r.role,
    status: "ONLINE",
    discovery_depth: 0,
    is_stp_root: false,
  }));
  const fakeLinks: TopologyLink[] = roleEdges.map((e, i) => ({
    id: i,
    src_device_id: indexByRole.get(e.srcRole)!,
    src_interface_id: null,
    dst_device_id: indexByRole.get(e.dstRole)!,
    dst_interface_id: null,
    source: "ROLE",
    label: null,
    confidence: 100,
    status: "UP",
    first_seen_at: "",
    last_seen_at: "",
    src_port: null,
    dst_port: null,
    src_stp_state: null,
    dst_stp_state: null,
    link_role: "HIERARCHICAL",
  }));
  // 구름(RoleCloudNode)은 160x100 SVG라 일반 장비 노드보다 훨씬 크다.
  const positionsByIndex = computeLayoutPositions(
    { nodes: fakeNodes, links: fakeLinks },
    mode,
    { autoLayoutNodeRadius: CLOUD_AUTO_LAYOUT_RADIUS },
  );
  // 배율을 다시 조절할 때 그래프가 한쪽으로 밀리지 않도록 배치 중심을 기준으로 계산한다.
  const rawPositions = [...positionsByIndex.values()];
  const centerX = rawPositions.reduce((sum, position) => sum + position.x, 0) / Math.max(rawPositions.length, 1);
  const centerY = rawPositions.reduce((sum, position) => sum + position.y, 0) / Math.max(rawPositions.length, 1);
  const positionsByRole = new Map<string, NodePosition>();
  roleSummaries.forEach((r, i) => {
    const p = positionsByIndex.get(i);
    if (p) {
      positionsByRole.set(r.role, {
        x: centerX + (p.x - centerX) * CLOUD_LAYOUT_SPACING_SCALE,
        y: centerY + (p.y - centerY) * CLOUD_LAYOUT_SPACING_SCALE,
      });
    }
  });
  return positionsByRole;
}

// [KOS20260922] "팝업 화면의 넓이를 전체 화면으로 수정" 요청 - Modal.tsx의
// .modal-content가 이미 `max-width: calc(100% - 32px)`로 clamp하므로, width에
// 실제 화면보다 항상 큰 값을 넘기면 어떤 해상도에서도 사실상 전체 화면 너비로
// 렌더링된다.
const FULLSCREEN_POPUP_WIDTH = 4000;
const ROLE_POPUP_WIDTH = FULLSCREEN_POPUP_WIDTH;
const DRILL_DOWN_POPUP_WIDTH = FULLSCREEN_POPUP_WIDTH;

// [KOS20260922] "구름 보기, 전체 보기 옵션을 제공해 주고, 구름 보기를 디폴트로
// 한다" 요청 - 작업 35에서 메인 화면을 구름 개요로 완전히 바꿨더니, 개별 장비를
// 전부 한 화면에서 보던 예전 방식(TopologyGraphView 전체 기능)을 찾을 수 없게
// 됐다는 지적. 화면을 아예 분리하는 대신 같은 화면 안에서 토글로 전환한다.
type ViewMode = "CLOUD" | "FULL";

// [KOS20260922] "CORE_SWITCH, DISTRIBUTION_SWITCH, FLOOR_SWITCH는 팝업에서 하위
// 스위치를 보여 줘" - 이 세 백본 Role의 팝업은 filterTopologyByRole()이 하위
// 스위치까지 포함해 실제 상하 계층이 있으므로 tree-vertical이 grid보다 알아보기
// 쉽다. ACCESS_SWITCH(및 그 외 Role)는 Role 내부 Peer 관계뿐이라 grid를 쓴다.
const BACKBONE_ROLES = new Set(["CORE_SWITCH", "DISTRIBUTION_SWITCH", "FLOOR_SWITCH"]);
// [KOS20260922] "ACCESS_SWITCH는 노드를 클릭하면 ... 팝업으로 하위 노드를 다시
// 조회해서 그래프로 보여 줘" - 이 Role의 팝업에서 노드 클릭 시 그 장비의
// 하위 노드(보통 단말)를 또 다른 팝업으로 띄운다. "DISTRIBUTION_SWITCH도/
// FLOOR_SWITCH도 ACCESS_SWITCH처럼 동작하도록" 요청에 따라 둘 다 추가한다 -
// filterTopologyByRole()의 하위 스위치/ARP 약한 링크 포함은 그대로 유지되고,
// 거기에 더해 개별 장비 클릭 시 드릴다운도 가능해진다. CORE_SWITCH도 동일하게
// 실제 하위 노드가 있을 때만 드릴다운을 연다.
const DRILL_DOWN_ROLES = new Set(["CORE_SWITCH", "DISTRIBUTION_SWITCH", "FLOOR_SWITCH", "ACCESS_SWITCH"]);

// [KOS20260922] "메인 화면에서 구름을 이동할 수 있도록, 이동한 상태도 유지되도록"
// 요청 - Role별 구름은 topology를 재조회할 때마다(주기적 재조회/재분류 등) 다시
// 계산되므로, 사용자가 드래그로 옮긴 위치를 Role 이름을 key로 localStorage에
// 저장해 뒀다가 다음 계산에도 그대로 사용한다. DiscoveryPage.tsx의 폼 저장과
// 동일한 패턴(프라이빗 모드 등으로 localStorage를 못 써도 화면 기능은 계속
// 동작해야 하므로 예외는 무시).
// 간격 정책 변경을 기존 저장 좌표에도 확실히 적용하기 위해 저장 key를 올린다.
// 사용자가 이후 직접 옮긴 위치는 새 key에 다시 저장되어 계속 유지된다.
const CLOUD_POSITIONS_STORAGE_KEY = "nms.topologyCloudPositions.v3";

type CloudPositions = Record<string, { x: number; y: number }>;

function loadStoredCloudPositions(): CloudPositions {
  try {
    const raw = window.localStorage.getItem(CLOUD_POSITIONS_STORAGE_KEY);
    return raw ? (JSON.parse(raw) as CloudPositions) : {};
  } catch {
    return {};
  }
}

function saveStoredCloudPosition(role: string, position: { x: number; y: number }) {
  try {
    const all = loadStoredCloudPositions();
    all[role] = position;
    window.localStorage.setItem(CLOUD_POSITIONS_STORAGE_KEY, JSON.stringify(all));
  } catch {
    // localStorage를 쓸 수 없어도(프라이빗 모드 등) 화면 기능 자체는 계속 동작해야 하므로 무시한다.
  }
}

function replaceStoredCloudPositions(positions: CloudPositions) {
  try {
    window.localStorage.setItem(CLOUD_POSITIONS_STORAGE_KEY, JSON.stringify(positions));
  } catch {
    // localStorage를 쓸 수 없어도(프라이빗 모드 등) 화면 기능 자체는 계속 동작해야 하므로 무시한다.
  }
}

// 저장된(드래그) 위치가 있으면 그것을 우선하고, 없는 Role만 배치 알고리즘이
// 계산한 좌표를 쓴다.
function mergeWithStoredPositions(computed: Map<string, NodePosition>, stored: CloudPositions): Map<string, NodePosition> {
  const merged = new Map(computed);
  for (const [role, pos] of Object.entries(stored)) {
    merged.set(role, pos);
  }
  return merged;
}

// [KOS20260922] "Topology 화면은 Device 구분을 구름으로 표시하고 관계를 표시" 요청에
// 따라, 메인 화면은 이제 개별 장비 그래프 대신 device_role 단위 구름 개요만 보여준다.
// 원래의 상세 그래프(배치/필터/검색/클릭 상세 등 모든 기능)는 TopologyGraphView로
// 옮겨졌고, 구름을 클릭하면 그 Role에 속한 장비만 모아 팝업으로 띄운다.
function buildCloudGraph(
  nodes: RoleCloudSummary[],
  edges: RoleCloudEdge[],
  positions: Map<string, NodePosition>,
): { nodes: Node[]; edges: Edge[] } {
  const flowNodes: Node[] = nodes.map((n) => ({
    id: n.role,
    type: "cloud",
    position: positions.get(n.role) ?? { x: 300, y: 0 },
    draggable: true,
    data: {
      label: n.role,
      color: ROLE_COLOR[n.role] ?? "#9ca3af",
      count: n.count,
      onlineCount: n.onlineCount,
    } as RoleCloudNodeData,
  }));
  const flowEdges: Edge[] = edges.map((e) => ({
    id: e.id,
    source: e.srcRole,
    target: e.dstRole,
    type: "bendable",
    label: `${e.count}개 연결`,
    // [KOS20260922] "링크 레이블이 갑자기 배경이 들어갔다" - 이 구름 개요 Edge는
    // (TopologyGraphView.tsx의 BendableEdge와 달리) React Flow 기본 Edge를 그대로
    // 쓰는데, 기본 EdgeText가 라벨 뒤에 흰색 배경 사각형을 그린다. 배경 없이
    // 검정 글자만 보이도록 끈다.
    labelShowBg: false,
    labelStyle: { fill: "#000000", fontWeight: 600 },
    animated: true,
    style: { stroke: "#3b82f6", strokeWidth: 2 },
    markerEnd: { type: MarkerType.ArrowClosed },
  }));
  return { nodes: flowNodes, edges: flowEdges };
}

export default function TopologyGraphPage() {
  const [topology, setTopology] = useState<Topology | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<ViewMode>("CLOUD");
  const [selectedRole, setSelectedRole] = useState<string | null>(null);
  const [drillDownDeviceId, setDrillDownDeviceId] = useState<number | null>(null);
  const [fullViewDrillDownDeviceId, setFullViewDrillDownDeviceId] = useState<number | null>(null);
  const [layoutMode, setLayoutMode] = useState<LayoutMode>("tree-vertical");
  const [nodes, setNodes] = useState<Node[]>([]);
  const [edges, setEdges] = useState<Edge[]>([]);
  const reactFlowInstanceRef = useRef<ReactFlowInstance | null>(null);

  const handleLinkCreated = useCallback((link: TopologyLink) => {
    setTopology((current) => current ? { ...current, links: [...current.links.filter((item) => item.id !== link.id), link] } : current);
  }, []);

  const handleLinkDeleted = useCallback((linkId: number) => {
    setTopology((current) => current ? { ...current, links: current.links.filter((link) => link.id !== linkId) } : current);
  }, []);

  useEffect(() => {
    api.getTopology().then(setTopology).catch((err) => setError(err.message));
  }, []);

  const { nodes: roleSummaries, edges: roleEdges } = useMemo(
    () => (topology ? buildRoleCloudGraph(topology) : { nodes: [], edges: [] }),
    [topology],
  );

  // topology가 재조회될 때만(예: 최초 로드) 위치를 다시 계산한다 - 이후 드래그로
  // 옮긴 위치는 onNodesChange가 관리하는 로컬 state + localStorage에 남고, 이
  // effect가 다시 도는 시점에도 저장된 값을 그대로 읽어와 유지한다. 배치 모드
  // 자체를 바꾸는 것은 아래 applyLayout()이 별도로 처리한다(여기서 layoutMode를
  // 의존성에 넣지 않는 이유).
  useEffect(() => {
    const computed = computeCloudLayoutPositions(roleSummaries, roleEdges, layoutMode);
    const positions = mergeWithStoredPositions(computed, loadStoredCloudPositions());
    const graph = buildCloudGraph(roleSummaries, roleEdges, positions);
    setNodes(graph.nodes);
    setEdges(graph.edges);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [roleSummaries, roleEdges]);

  // React Flow는 nodes를 prop으로만 넘기면 드래그가 내부 상태에 반영되지 않고
  // 다음 리렌더에서 원래 위치로 되돌아간다 - applyNodeChanges로 로컬 state를
  // 직접 갱신해야 드래그가 실제로 반영된다(TopologyGraphView.tsx와 동일 패턴).
  // 드래그가 "끝난" 시점(dragging: false)에만 localStorage에 저장해 드래그
  // 도중 매 프레임 쓰기를 피한다.
  const onNodesChange = useCallback((changes: NodeChange[]) => {
    setNodes((current) => applyNodeChanges(changes, current));
    for (const change of changes) {
      if (change.type === "position" && change.position && change.dragging === false) {
        saveStoredCloudPosition(change.id, change.position);
      }
    }
  }, []);

  const handleEdgeBendChange = useCallback((edgeId: string, bend: BendPoint) => {
    setEdges((current) =>
      current.map((edge) => (edge.id === edgeId ? { ...edge, data: { ...edge.data, bend } } : edge)),
    );
  }, []);

  const interactiveEdges = useMemo(
    () => edges.map((edge) => ({ ...edge, data: { ...edge.data, onBendChange: handleEdgeBendChange } })),
    [edges, handleEdgeBendChange],
  );

  // [KOS20260922] "자동 배치, 원 배치, 격자 배치, 수평 트리 배치, 수직 트리 배치를
  // 추가해 줘" 요청 - 배치를 직접 선택하는 것은 "지금 다시 배치해 줘"라는 명시적
  // 의도이므로, 그동안 드래그로 저장해 둔 위치를 이번 배치 결과로 덮어써 저장한다
  // (TopologyGraphView.tsx의 "배치" 선택도 재계산 시 드래그 위치를 초기화하는
  // 것과 동일한 동작).
  const applyLayout = (mode: LayoutMode) => {
    setLayoutMode(mode);
    const computed = computeCloudLayoutPositions(roleSummaries, roleEdges, mode);
    const asRecord: CloudPositions = {};
    computed.forEach((pos, role) => {
      asRecord[role] = pos;
    });
    replaceStoredCloudPositions(asRecord);
    const graph = buildCloudGraph(roleSummaries, roleEdges, computed);
    setNodes(graph.nodes);
    setEdges(graph.edges);
  };

  const selectedSummary = selectedRole ? roleSummaries.find((r) => r.role === selectedRole) : null;
  const roleTopology = topology && selectedRole ? filterTopologyByRole(topology, selectedRole) : null;

  const closeRolePopup = () => {
    setSelectedRole(null);
    setDrillDownDeviceId(null); // 다음에 다른 Role을 열었을 때 이전 드릴다운이 남지 않게 한다.
  };

  const drillDownDevice =
    topology && drillDownDeviceId != null ? topology.nodes.find((n) => n.id === drillDownDeviceId) ?? null : null;
  const drillDownTopology =
    topology && drillDownDeviceId != null ? filterTopologyByDeviceChildren(topology, drillDownDeviceId) : null;

  const openDrillDownIfChildren = (node: RenderNode) => {
    if (!topology) return;
    const childTopology = filterTopologyByDeviceChildren(topology, node.id);
    const hasChildNode = childTopology.nodes.some((child) => child.id !== node.id);
    setDrillDownDeviceId(hasChildNode ? node.id : null);
  };

  // [KOS20260922] "전체 보기에서 노드를 클릭하면 자식, 그 자식, 단말 노드까지
  // 전체를 보여 주는 팝업" 요청 - 구름 클릭 시 뜨는 팝업과 같은 Modal +
  // TopologyGraphView 패턴을 재사용하되, 한 단계 아래 이웃만 보여주는
  // filterTopologyByDeviceChildren() 대신 재귀적으로 단말까지 모으는
  // filterTopologyByDeviceDescendants()를 쓴다. 하위 노드가 전혀 없으면(단말
  // 노드 자신을 클릭한 경우 등) 팝업을 띄우지 않는다.
  const fullViewDrillDownDevice =
    topology && fullViewDrillDownDeviceId != null
      ? topology.nodes.find((n) => n.id === fullViewDrillDownDeviceId) ?? null
      : null;
  const fullViewDrillDownTopology =
    topology && fullViewDrillDownDeviceId != null
      ? filterTopologyByDeviceDescendants(topology, fullViewDrillDownDeviceId)
      : null;

  const openFullViewDrillDownIfDescendants = (node: RenderNode) => {
    if (!topology) return;
    const descendantTopology = filterTopologyByDeviceDescendants(topology, node.id);
    const hasDescendant = descendantTopology.nodes.some((child) => child.id !== node.id);
    setFullViewDrillDownDeviceId(hasDescendant ? node.id : null);
  };

  return (
    <div>
      <div className="page-header">
        <h1>Topology</h1>
        <span className="muted">
          {topology ? `${topology.nodes.length} nodes · ${topology.links.length} links · Role 구름 ${roleSummaries.length}개` : ""}
        </span>
      </div>
      {error && <div className="error-banner">{error}</div>}

      <div className="toolbar">
        <span className="muted" style={{ fontSize: 13 }}>
          보기:
        </span>
        <button className={viewMode === "CLOUD" ? "btn btn-primary" : "btn"} onClick={() => setViewMode("CLOUD")}>
          구름 보기
        </button>
        <button className={viewMode === "FULL" ? "btn btn-primary" : "btn"} onClick={() => setViewMode("FULL")}>
          전체 보기
        </button>
      </div>

      {viewMode === "CLOUD" ? (
        <>
          <span className="muted" style={{ fontSize: 12 }}>
            Device 구분(Role)별 구름입니다. 우클릭 드래그로 확대하고 Shift+우클릭 드래그로 축소합니다. 링크는 좌클릭 드래그로 굴곡을 조정할 수 있습니다.
          </span>

          <div className="toolbar">
            <label htmlFor="cloud-layout-select" className="muted" style={{ fontSize: 13 }}>
              배치:
            </label>
            <select
              id="cloud-layout-select"
              value={layoutMode}
              onChange={(e) => applyLayout(e.target.value as LayoutMode)}
            >
              {LAYOUT_MODES.map((mode) => (
                <option key={mode} value={mode}>
                  {LAYOUT_LABELS[mode]}
                </option>
              ))}
            </select>
          </div>

          <TopologyBoxZoom
            flowInstanceRef={reactFlowInstanceRef}
            className="card"
            style={{ height: "70vh", width: "100%", padding: 0, overflow: "hidden", marginTop: 10 }}
          >
            <ReactFlow
              nodes={nodes}
              edges={interactiveEdges}
              nodeTypes={NODE_TYPES}
              edgeTypes={EDGE_TYPES}
              onInit={(instance) => {
                reactFlowInstanceRef.current = instance;
              }}
              onNodesChange={onNodesChange}
              nodesDraggable
              panOnDrag
              zoomOnScroll
              zoomOnPinch
              onNodeClick={(_, node) => setSelectedRole(node.id)}
              fitView
            >
              <Background />
              <Controls showInteractive={false} />
            </ReactFlow>
          </TopologyBoxZoom>

          <div className="card">
            <div className="tree-group-title">범례 (Role)</div>
            <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
              {roleSummaries.map((r) => (
                <span key={r.role} style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12 }}>
                  <span style={{ width: 12, height: 12, borderRadius: 4, background: ROLE_COLOR[r.role] ?? "#9ca3af", display: "inline-block" }} />
                  {r.role} ({r.count}대)
                </span>
              ))}
            </div>
          </div>
        </>
      ) : (
        topology && (
          <TopologyGraphView
            key="full"
            topology={topology}
            title="Topology (전체 보기)"
            defaultLayoutMode="tree-vertical"
            onLinkCreated={handleLinkCreated}
            onLinkDeleted={handleLinkDeleted}
            onDrillDownNode={openFullViewDrillDownIfDescendants}
          />
        )
      )}

      {fullViewDrillDownTopology && fullViewDrillDownDevice && (
        <Modal onClose={() => setFullViewDrillDownDeviceId(null)} width={DRILL_DOWN_POPUP_WIDTH}>
          <TopologyGraphView
            key={fullViewDrillDownDevice.id}
            topology={fullViewDrillDownTopology}
            onLinkCreated={handleLinkCreated}
            onLinkDeleted={handleLinkDeleted}
            title={`${fullViewDrillDownDevice.hostname || fullViewDrillDownDevice.management_ip || `#${fullViewDrillDownDevice.id}`} 하위 전체 (${fullViewDrillDownTopology.nodes.length - 1}대)`}
            defaultLayoutMode="tree-vertical"
            headerExtra={
              <button className="btn" onClick={() => setFullViewDrillDownDeviceId(null)}>
                닫기 ✕
              </button>
            }
          />
        </Modal>
      )}

      {roleTopology && selectedSummary && (
        <Modal onClose={closeRolePopup} width={ROLE_POPUP_WIDTH}>
          <TopologyGraphView
            key={selectedSummary.role}
            topology={roleTopology}
            title={`${selectedSummary.role} 상세 (${selectedSummary.count}대)`}
            defaultLayoutMode={BACKBONE_ROLES.has(selectedSummary.role) ? "tree-vertical" : "grid"}
            onLinkCreated={handleLinkCreated}
            onLinkDeleted={handleLinkDeleted}
            onDrillDownNode={
              DRILL_DOWN_ROLES.has(selectedSummary.role) ? openDrillDownIfChildren : undefined
            }
            headerExtra={
              <button className="btn" onClick={closeRolePopup}>
                닫기 ✕
              </button>
            }
          />

          {drillDownTopology && drillDownDevice && (
            <Modal onClose={() => setDrillDownDeviceId(null)} width={DRILL_DOWN_POPUP_WIDTH}>
              <TopologyGraphView
                key={drillDownDevice.id}
                topology={drillDownTopology}
                onLinkCreated={handleLinkCreated}
                onLinkDeleted={handleLinkDeleted}
                title={`${drillDownDevice.hostname || drillDownDevice.management_ip || `#${drillDownDevice.id}`} 하위 장비 (${drillDownTopology.nodes.length - 1}대)`}
                defaultLayoutMode="tree-vertical"
                headerExtra={
                  <button className="btn" onClick={() => setDrillDownDeviceId(null)}>
                    닫기 ✕
                  </button>
                }
              />
            </Modal>
          )}
        </Modal>
      )}
    </div>
  );
}
