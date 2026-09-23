import { useEffect, useMemo, useRef, useState } from "react";
import { api, Topology, TopologyLink, TopologyNode } from "../api/client";
import DeviceDetailContent from "../components/DeviceDetailContent";
import Modal from "../components/Modal";
import LinkPingDiagnostics from "../components/LinkPingDiagnostics";
import NewTopologyCanvas, { GRAPH_STYLE_LABEL, GraphStyle } from "../components/NewTopologyCanvas";
import TopologyContextMenu from "../components/TopologyContextMenu";
import TopologyGraphView from "../components/TopologyGraphView";
import { computeUpstreamPath, filterTopologyByDeviceDescendants } from "../lib/topologyRoleClouds";
import { DEFAULT_EXPANDED_GROUPS, GroupBy, groupKeyOf } from "../lib/topologyDrilldown";

// [KOS20260923] "New Topology" 요청 - 기존 Topology(TopologyGraphPage.tsx 등)는
// 전혀 건드리지 않고, 별도 메뉴/페이지로 3-패널(Navigator/Canvas/Detail) 레이아웃을
// 새로 만든다. docs/design/new-topology-design.md의 Phase 1(MVP) 범위 + 사용자가
// 제시한 와이어프레임(검색/View 타입 탭/상태 하단바) + 그래프 스타일 3종 선택
// 기능을 구현한다. 가능한 한 기존 컴포넌트/순수 함수를 그대로 재사용한다
// (DeviceDetailContent, TopologyContextMenu, filterTopologyByDeviceDescendants,
// TopologyGraphView는 드릴다운 팝업 내용물로만 재사용).

type ViewType = "PHYSICAL" | "L2" | "L3" | "VLAN";

const VIEW_TYPE_LABEL: Record<ViewType, string> = {
  PHYSICAL: "Physical",
  L2: "L2",
  L3: "L3",
  VLAN: "VLAN",
};

// L2 근거로 보는 소스: 스위칭 계층에서 나온 증거(LLDP/CDP/FDB 계열/STP)만 포함하고,
// IP 기반으로만 추정한 ARP-only/IP_SCAN 근거는 뺀다.
const L2_SOURCES = new Set(["LLDP", "CDP", "FDB", "FDB_ARP", "STP"]);
// L3 뷰: 실제 라우팅 테이블 그래프는 아직 없어(설계서 23장 Phase 4), 대신
// "L3 능력이 있는 장비 + 그 장비끼리의 연결"로 근사한다 - 정확한 라우팅 경로는
// 아니지만 최소한 "이 망의 L3 장비가 서로 어떻게 연결돼 있는지"는 보여준다.
const L3_DEVICE_TYPES = new Set(["ROUTER", "L3_SWITCH", "L3_POE_SWITCH"]);

const DEVICE_ROLE_ORDER = [
  "CORE_SWITCH",
  "DISTRIBUTION_SWITCH",
  "FLOOR_SWITCH",
  "ACCESS_SWITCH",
  "EDGE_DEVICE",
  "SERVER",
  "ENDPOINT",
  "UNKNOWN",
];
const ROLE_LABEL: Record<string, string> = {
  CORE_SWITCH: "Core Switch",
  DISTRIBUTION_SWITCH: "Distribution Switch",
  FLOOR_SWITCH: "Floor Switch",
  ACCESS_SWITCH: "Access Switch",
  EDGE_DEVICE: "Edge Device (AP/Hub)",
  SERVER: "Server",
  ENDPOINT: "Endpoint",
  UNKNOWN: "Unknown",
};

// [KOS20260923] "Network Navigator는 Role별, Device Type별로 선택해서 볼 수
// 있도록" 요청 - 8장(device_type.py) 분류값 기준 그룹핑 옵션을 추가한다.
const DEVICE_TYPE_ORDER = [
  "ROUTER",
  "L3_SWITCH",
  "L3_POE_SWITCH",
  "L2_SWITCH",
  "L2_POE_SWITCH",
  "ACCESS_POINT",
  "HUB",
  "IP_CAMERA",
  "WINDOWS_PC",
  "LINUX_PC",
  "MAC_PC",
  "UNKNOWN",
];
const DEVICE_TYPE_LABEL: Record<string, string> = {
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

// 하단 상태바 4분류(Normal/Warning/Critical/Unknown)는 StatusBadge.tsx의 배지
// 색상 규칙(ONLINE=녹색/STALE=황색/OFFLINE=회색-치명/UNKNOWN=보라)과 같은
// 의미로 맞춘다.
function statusBucket(status: string): "Normal" | "Warning" | "Critical" | "Unknown" {
  if (status === "ONLINE") return "Normal";
  if (status === "STALE") return "Warning";
  if (status === "OFFLINE") return "Critical";
  return "Unknown";
}

type ContextMenuState = { node: TopologyNode; x: number; y: number } | null;

// [KOS20260923] "링크를 클릭하면 링크 정보도 조회되도록" 요청 - 우측 패널에
// 장치 상세와 같은 자리에 링크 상세를 보여준다. 구름 단계에서 여러 실제 링크가
// 하나의 엣지로 뭉쳐 있을 수 있어 배열로 받아 각각 나열한다.
function LinkDetailPanel({
  links,
  topology,
  onClose,
}: {
  links: TopologyLink[];
  topology: Topology | null;
  onClose: () => void;
}) {
  const nodeLabel = (id: number): string => {
    const n = topology?.nodes.find((x) => x.id === id);
    return n ? n.hostname || n.management_ip || `#${id}` : `#${id}`;
  };
  return (
    <div style={{ padding: 16 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h3 style={{ margin: 0 }}>링크 정보{links.length > 1 ? ` (${links.length}개)` : ""}</h3>
        <button className="btn" onClick={onClose}>
          닫기 ✕
        </button>
      </div>
      {links.map((l) => (
        <div key={l.id} className="card" style={{ marginTop: 10, fontSize: 13 }}>
          <div style={{ fontWeight: 600 }}>
            {nodeLabel(l.src_device_id)} ({l.src_port ?? "?"}) → {nodeLabel(l.dst_device_id)} ({l.dst_port ?? "?"})
          </div>
          <div className="muted" style={{ marginTop: 4 }}>
            근거: {l.source} · Confidence: {l.confidence} · 상태: {l.status} · 관계: {l.link_role}
          </div>
          {(l.src_stp_state || l.dst_stp_state) && (
            <div className="muted">
              STP: src={l.src_stp_state ?? "-"} / dst={l.dst_stp_state ?? "-"}
            </div>
          )}
          {l.label && <div className="muted">Label: {l.label}</div>}
          <div className="muted" style={{ fontSize: 11, marginTop: 4 }}>
            최초 발견: {l.first_seen_at} · 최근 확인: {l.last_seen_at}
          </div>
        </div>
      ))}
      {links.length > 0 && topology && (
        <div style={{ marginTop: 16 }}>
          <LinkPingDiagnostics links={links} topology={topology} />
        </div>
      )}
    </div>
  );
}

export default function NewTopologyPage() {
  const [topology, setTopology] = useState<Topology | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [viewType, setViewType] = useState<ViewType>("PHYSICAL");
  const [graphStyle, setGraphStyle] = useState<GraphStyle>(1);
  const [selectedNodeId, setSelectedNodeId] = useState<number | null>(null);
  const [selectedLink, setSelectedLink] = useState<TopologyLink[] | null>(null);
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null);
  const [groupBy, setGroupBy] = useState<GroupBy>("ROLE");
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set(DEFAULT_EXPANDED_GROUPS.ROLE));
  const [hiddenSources, setHiddenSources] = useState<Set<string>>(new Set());
  const [rightPanelCollapsed, setRightPanelCollapsed] = useState(false);
  const [rightPanelWidth, setRightPanelWidth] = useState(380);
  const panelResizeRef = useRef<{ startX: number; startWidth: number } | null>(null);
  const [contextMenu, setContextMenu] = useState<ContextMenuState>(null);
  const [upstreamPopup, setUpstreamPopup] = useState<{ device: TopologyNode; path: { id: number; label: string }[] } | null>(
    null,
  );
  const [downstreamDeviceId, setDownstreamDeviceId] = useState<number | null>(null);

  useEffect(() => {
    api.getTopology().then(setTopology).catch((e) => setError(e.message));
  }, []);

  const viewTopology = useMemo((): Topology | null => {
    if (!topology) return null;
    if (viewType === "L2") {
      return { nodes: topology.nodes, links: topology.links.filter((l) => L2_SOURCES.has(l.source)) };
    }
    if (viewType === "L3") {
      const nodes = topology.nodes.filter((n) => L3_DEVICE_TYPES.has(n.device_type));
      const ids = new Set(nodes.map((n) => n.id));
      return { nodes, links: topology.links.filter((l) => ids.has(l.src_device_id) && ids.has(l.dst_device_id)) };
    }
    if (viewType === "VLAN") {
      return { nodes: [], links: [] };
    }
    return topology;
  }, [topology, viewType]);

  // [KOS20260923] "링크 선택 옵션도 제공해 줘" 요청 - TopologyGraphView.tsx의
  // 근거(evidence source) 체크박스와 같은 방식으로, 실제 데이터에 존재하는 근거
  // 종류만 옵션으로 뽑아 켜고 끌 수 있게 한다. 기본은 전부 켜짐(hiddenSources 비어있음)
  // 이라 기존 동작과 동일하다.
  const availableSources = useMemo(() => {
    const set = new Set<string>();
    for (const l of topology?.links ?? []) set.add(l.source);
    return Array.from(set).sort();
  }, [topology]);

  const toggleSource = (source: string) => {
    setHiddenSources((prev) => {
      const next = new Set(prev);
      if (next.has(source)) next.delete(source);
      else next.add(source);
      return next;
    });
  };

  const toggleAllSources = () => {
    setHiddenSources((prev) => (prev.size === 0 ? new Set(availableSources) : new Set()));
  };

  const canvasTopology = useMemo((): Topology | null => {
    if (!viewTopology) return null;
    if (hiddenSources.size === 0) return viewTopology;
    return { nodes: viewTopology.nodes, links: viewTopology.links.filter((l) => !hiddenSources.has(l.source)) };
  }, [viewTopology, hiddenSources]);

  const handleToggleGroup = (group: string) => {
    setExpandedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(group)) next.delete(group);
      else next.add(group);
      return next;
    });
  };

  // "Role별/Device Type별" 전환 - 그룹 값 자체가 완전히 달라지므로 펼침 상태와
  // 선택된 링크(구름 id가 그룹 기준에 종속)를 그 기준의 기본값/초기화로 되돌린다.
  const handleSetGroupBy = (next: GroupBy) => {
    setGroupBy(next);
    setExpandedGroups(new Set(DEFAULT_EXPANDED_GROUPS[next]));
    setSelectedLink(null);
    setSelectedEdgeId(null);
  };

  // 좌측 Navigator에서 장비를 고르면, 그 장비의 그룹이 접혀 있어 캔버스에 구름
  // 안에 숨어 있더라도 자동으로 펼쳐서 실제로 화면에 나타나게 한다.
  const handleSelectNode = (node: TopologyNode) => {
    setSelectedNodeId(node.id);
    setSelectedLink(null);
    setSelectedEdgeId(null);
    setRightPanelCollapsed(false);
    const group = groupKeyOf(node, groupBy);
    setExpandedGroups((prev) => (prev.has(group) ? prev : new Set(prev).add(group)));
  };

  const handleEdgeClick = (links: TopologyLink[], edgeId: string) => {
    setSelectedLink(links);
    setSelectedEdgeId(edgeId);
    setSelectedNodeId(null);
    setRightPanelCollapsed(false);
  };

  const allGroups = useMemo(() => {
    return new Set((topology?.nodes ?? []).map((n) => groupKeyOf(n, groupBy)));
  }, [topology, groupBy]);

  const navigatorGroups = useMemo(() => {
    if (!topology) return [];
    const q = search.trim().toLowerCase();
    const filtered = topology.nodes.filter((n) => {
      if (!q) return true;
      return (n.hostname ?? "").toLowerCase().includes(q) || (n.management_ip ?? "").toLowerCase().includes(q);
    });
    const groups = new Map<string, TopologyNode[]>();
    for (const n of filtered) {
      const key = groupKeyOf(n, groupBy);
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key)!.push(n);
    }
    const order = groupBy === "DEVICE_TYPE" ? DEVICE_TYPE_ORDER : DEVICE_ROLE_ORDER;
    const label = groupBy === "DEVICE_TYPE" ? DEVICE_TYPE_LABEL : ROLE_LABEL;
    return order
      .filter((key) => groups.has(key))
      .map((key) => ({
        role: key,
        label: label[key] ?? key,
        devices: groups.get(key)!.sort((a, b) => (a.hostname || "").localeCompare(b.hostname || "")),
      }));
  }, [topology, search, groupBy]);

  const statusCounts = useMemo(() => {
    const counts = { Normal: 0, Warning: 0, Critical: 0, Unknown: 0 };
    for (const n of topology?.nodes ?? []) counts[statusBucket(n.status)] += 1;
    return counts;
  }, [topology]);

  const handleShowUpstream = (node: TopologyNode) => {
    if (!topology) return;
    setUpstreamPopup({ device: node, path: computeUpstreamPath(topology, node.id) });
  };

  const downstreamTopology =
    topology && downstreamDeviceId != null ? filterTopologyByDeviceDescendants(topology, downstreamDeviceId) : null;
  const downstreamDevice =
    topology && downstreamDeviceId != null ? topology.nodes.find((n) => n.id === downstreamDeviceId) ?? null : null;

  const handleShowDownstream = (node: TopologyNode) => {
    if (!topology) return;
    const sub = filterTopologyByDeviceDescendants(topology, node.id);
    if (sub.nodes.length <= 1) {
      setUpstreamPopup({ device: node, path: [{ id: node.id, label: "하위 장비가 없습니다." }] });
      return;
    }
    setDownstreamDeviceId(node.id);
  };

  const startPanelResize = (event: React.PointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    panelResizeRef.current = { startX: event.clientX, startWidth: rightPanelWidth };
    event.currentTarget.setPointerCapture(event.pointerId);
  };

  const movePanelResize = (event: React.PointerEvent<HTMLDivElement>) => {
    const resize = panelResizeRef.current;
    if (!resize) return;
    const maxWidth = Math.max(420, Math.floor(window.innerWidth * 0.65));
    setRightPanelWidth(Math.min(maxWidth, Math.max(320, resize.startWidth + resize.startX - event.clientX)));
  };

  const stopPanelResize = (event: React.PointerEvent<HTMLDivElement>) => {
    panelResizeRef.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
  };

  return (
    <div>
      <div className="page-header">
        <h1>New Topology</h1>
        <span className="muted">
          {topology ? `${topology.nodes.length} nodes · ${topology.links.length} links` : ""}
        </span>
      </div>
      {error && <div className="error-banner">{error}</div>}

      {/* 상단바: 검색 + View 타입 탭 + 그래프 스타일 선택 */}
      <div className="toolbar" style={{ justifyContent: "space-between" }}>
        <input
          placeholder="🔍 Search (hostname/IP)"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          style={{ width: 220 }}
        />
        <div className="toolbar" style={{ gap: 4 }}>
          {(Object.keys(VIEW_TYPE_LABEL) as ViewType[]).map((vt) => (
            <button key={vt} className={viewType === vt ? "btn btn-primary" : "btn"} onClick={() => setViewType(vt)}>
              {VIEW_TYPE_LABEL[vt]}
            </button>
          ))}
        </div>
        <select value={graphStyle} onChange={(e) => setGraphStyle(Number(e.target.value) as GraphStyle)}>
          {([1, 2, 3] as GraphStyle[]).map((s) => (
            <option key={s} value={s}>
              {GRAPH_STYLE_LABEL[s]}
            </option>
          ))}
        </select>
      </div>

      {/* 링크(근거) 필터 + 펼침 단계 조작 - TopologyGraphView.tsx의 "근거 표시:" 체크박스와 같은 패턴 */}
      <div className="toolbar" style={{ fontSize: 12, flexWrap: "wrap", gap: "4px 12px" }}>
        <span className="muted">링크 표시:</span>
        <label style={{ display: "flex", alignItems: "center", gap: 4 }}>
          <input type="checkbox" checked={hiddenSources.size === 0} onChange={toggleAllSources} />
          전체
        </label>
        {availableSources.map((src) => (
          <label key={src} style={{ display: "flex", alignItems: "center", gap: 4 }}>
            <input type="checkbox" checked={!hiddenSources.has(src)} onChange={() => toggleSource(src)} />
            {src}
          </label>
        ))}
        <span className="muted" style={{ marginLeft: 12 }}>
          단계별 표시: 구름을 클릭하면 펼쳐집니다.
        </span>
        <button className="btn" style={{ padding: "2px 8px" }} onClick={() => setExpandedGroups(new Set(allGroups))}>
          전체 펼치기
        </button>
        <button className="btn" style={{ padding: "2px 8px" }} onClick={() => setExpandedGroups(new Set(DEFAULT_EXPANDED_GROUPS[groupBy]))}>
          요약 보기
        </button>
      </div>

      {/* 3-패널: 좌 Navigator / 중앙 Canvas / 우 Device Detail(접기/펼치기 가능) */}
      <div
        className="card"
        style={{
          display: "grid",
          gridTemplateColumns: `220px 1fr ${rightPanelCollapsed ? 32 : rightPanelWidth}px`,
          gap: 0,
          padding: 0,
          height: "70vh",
          overflow: "hidden",
        }}
      >
        <div style={{ borderRight: "1px solid var(--border, #e5e7eb)", overflowY: "auto", padding: 10 }}>
          <div className="tree-group-title">Network Navigator</div>
          <div className="toolbar" style={{ gap: 4, marginBottom: 8 }}>
            <button
              className={groupBy === "ROLE" ? "btn btn-primary" : "btn"}
              style={{ padding: "2px 8px", fontSize: 11 }}
              onClick={() => handleSetGroupBy("ROLE")}
            >
              Role별
            </button>
            <button
              className={groupBy === "DEVICE_TYPE" ? "btn btn-primary" : "btn"}
              style={{ padding: "2px 8px", fontSize: 11 }}
              onClick={() => handleSetGroupBy("DEVICE_TYPE")}
            >
              Type별
            </button>
          </div>
          {navigatorGroups.map((g) => (
            <div key={g.role} style={{ marginBottom: 10 }}>
              <div className="muted" style={{ fontSize: 12, fontWeight: 600 }}>
                {g.label} ({g.devices.length})
              </div>
              {g.devices.map((d) => (
                <div
                  key={d.id}
                  onClick={() => handleSelectNode(d)}
                  style={{
                    padding: "3px 6px",
                    fontSize: 12,
                    cursor: "pointer",
                    borderRadius: 4,
                    background: selectedNodeId === d.id ? "#dbeafe" : undefined,
                  }}
                >
                  {d.hostname || d.management_ip || `#${d.id}`}
                </div>
              ))}
            </div>
          ))}
          {navigatorGroups.length === 0 && <div className="muted" style={{ fontSize: 12 }}>장비 없음</div>}
        </div>

        <div style={{ position: "relative" }}>
          {viewType === "VLAN" ? (
            <div className="muted" style={{ padding: 20 }}>
              VLAN 뷰는 준비 중입니다 - 인터페이스별 VLAN 소속 정보를 Topology
              응답에 포함하는 백엔드 작업이 필요합니다(설계서 23.5.4절 참고).
              지금은 장비 상세 화면의 Interfaces 탭에서 개별 포트의 VLAN을
              확인할 수 있습니다.
            </div>
          ) : canvasTopology ? (
            <NewTopologyCanvas
              topology={canvasTopology}
              style={graphStyle}
              groupBy={groupBy}
              expandedGroups={expandedGroups}
              onToggleGroup={handleToggleGroup}
              selectedNodeId={selectedNodeId}
              onNodeClick={handleSelectNode}
              onNodeContextMenu={(node, x, y) => setContextMenu({ node, x, y })}
              selectedEdgeId={selectedEdgeId}
              onEdgeClick={handleEdgeClick}
              onPaneClick={() => {
                setContextMenu(null);
                setSelectedLink(null);
                setSelectedEdgeId(null);
              }}
            />
          ) : (
            <div className="muted" style={{ padding: 20 }}>불러오는 중...</div>
          )}
        </div>

        {/* [KOS20260923] "우측 장치 정보 영역은 늘었다 죽었다 할 수 있도록" 요청 -
            중앙 그래프를 더 넓게 보고 싶을 때 우측 상세 패널을 접을 수 있게 한다. */}
        {rightPanelCollapsed ? (
          <div
            style={{ borderLeft: "1px solid var(--border, #e5e7eb)", display: "flex", justifyContent: "center", paddingTop: 10 }}
          >
            <button className="btn" style={{ padding: "4px 6px" }} onClick={() => setRightPanelCollapsed(false)} title="장치 정보 펼치기">
              ◀
            </button>
          </div>
        ) : (
          <div style={{ borderLeft: "1px solid var(--border, #e5e7eb)", overflow: "hidden", position: "relative" }}>
            <div
              role="separator"
              aria-label="장치 정보 패널 너비 조절"
              aria-orientation="vertical"
              title="좌우로 드래그하여 장치 정보 패널 너비 조절"
              onPointerDown={startPanelResize}
              onPointerMove={movePanelResize}
              onPointerUp={stopPanelResize}
              onPointerCancel={stopPanelResize}
              style={{
                position: "absolute",
                left: 0,
                top: 0,
                bottom: 0,
                width: 8,
                cursor: "col-resize",
                zIndex: 3,
                touchAction: "none",
                background: panelResizeRef.current ? "rgba(37, 99, 235, 0.25)" : "transparent",
              }}
            />
            <button
              className="btn"
              style={{ position: "absolute", top: 8, right: 8, padding: "2px 6px", zIndex: 1 }}
              onClick={() => setRightPanelCollapsed(true)}
              title="장치 정보 접기"
            >
              ▶
            </button>
            <div style={{ height: "100%", overflowY: "auto" }}>
              {selectedLink ? (
                <LinkDetailPanel
                  links={selectedLink}
                  topology={topology}
                  onClose={() => {
                    setSelectedLink(null);
                    setSelectedEdgeId(null);
                  }}
                />
              ) : selectedNodeId != null ? (
                <DeviceDetailContent key={selectedNodeId} deviceId={selectedNodeId} />
              ) : (
                <div className="muted" style={{ padding: 20 }}>
                  장비 또는 링크를 선택하면 상세 정보가 여기에 표시됩니다.
                </div>
              )}
            </div>
          </div>
        )}
      </div>

      {/* 하단 상태바 */}
      <div className="toolbar" style={{ fontSize: 13 }}>
        <span>
          <span className="badge badge-online">●</span> Normal {statusCounts.Normal}
        </span>
        <span>
          <span className="badge badge-stale">●</span> Warning {statusCounts.Warning}
        </span>
        <span>
          <span className="badge badge-offline">●</span> Critical {statusCounts.Critical}
        </span>
        <span>
          <span className="badge badge-unknown">●</span> Unknown {statusCounts.Unknown}
        </span>
      </div>

      {contextMenu && (
        <TopologyContextMenu
          x={contextMenu.x}
          y={contextMenu.y}
          onClose={() => setContextMenu(null)}
          items={[
            { label: "장비 상세 보기", onClick: () => setSelectedNodeId(contextMenu.node.id) },
            { label: "Show Upstream", onClick: () => handleShowUpstream(contextMenu.node) },
            { label: "Show Downstream", onClick: () => handleShowDownstream(contextMenu.node) },
            { label: "Ping / Traceroute (상세 패널에서)", onClick: () => setSelectedNodeId(contextMenu.node.id) },
          ]}
        />
      )}

      {upstreamPopup && (
        <Modal onClose={() => setUpstreamPopup(null)} width={480}>
          <div className="page-header">
            <h1>{upstreamPopup.device.hostname || upstreamPopup.device.management_ip} 상위 경로 (Core 방향)</h1>
            <button className="btn" onClick={() => setUpstreamPopup(null)}>
              닫기 ✕
            </button>
          </div>
          <div className="card">
            {upstreamPopup.path.length > 1 ? (
              <p>{upstreamPopup.path.map((p) => p.label).join(" → ")}</p>
            ) : (
              <p className="muted">{upstreamPopup.path[0]?.label ?? "상위 경로를 찾을 수 없습니다."}</p>
            )}
          </div>
        </Modal>
      )}

      {downstreamTopology && downstreamDevice && (
        <Modal onClose={() => setDownstreamDeviceId(null)} width={1400}>
          <TopologyGraphView
            key={downstreamDevice.id}
            topology={downstreamTopology}
            title={`${downstreamDevice.hostname || downstreamDevice.management_ip} 하위 전체 (${downstreamTopology.nodes.length - 1}대)`}
            defaultLayoutMode="tree-vertical"
            headerExtra={
              <button className="btn" onClick={() => setDownstreamDeviceId(null)}>
                닫기 ✕
              </button>
            }
          />
        </Modal>
      )}
    </div>
  );
}
