import { Topology, TopologyLink, TopologyNode } from "../api/client";

// [KOS20260923] "노드를 단계별로 펼치도록 구현이 안되고 모든 노드와 모든 링크가
// 그려져서 엄청 느리다" 지적 - 최초 구현은 181개 노드/1572개 링크를 항상 통째로
// ReactFlow에 넘겨 렌더링해, 설계서(new-topology-design.md) §7이 요구한
// "Zoom Level 1(요약)→2(스위치)→3(포트/단말)" 점진적 노출이 전혀 없었다. 이
// 함수는 topologyRoleClouds.ts의 buildRoleCloudGraph()와 같은 "그룹 단위 구름
// 집계" 개념을 New Topology의 상시 캔버스 기본 화면에 실제로 적용해, 펼쳐진
// 그룹만 개별 장비로 보여주고 나머지는 구름 1개로 접어 렌더링 노드 수 자체를
// 줄인다(성능 문제의 근본 원인 해결).
//
// [KOS20260923] "Network Navigator는 Role별, Device Type별로 선택해서 볼 수
// 있도록" 요청 - 구름으로 묶는 기준을 device_role 고정에서 device_role/
// device_type 중 선택 가능하게 일반화한다.
export type GroupBy = "ROLE" | "DEVICE_TYPE";

export function groupKeyOf(node: TopologyNode, groupBy: GroupBy): string {
  if (groupBy === "DEVICE_TYPE") return node.device_type || "UNKNOWN";
  return node.device_role || "UNKNOWN";
}

export type DisplayNode =
  | { kind: "device"; id: string; deviceId: number; node: TopologyNode }
  | { kind: "cloud"; id: string; group: string; count: number; onlineCount: number; memberIds: number[] };

export interface DisplayEdge {
  id: string;
  sourceId: string;
  targetId: string;
  count: number;
  links: TopologyLink[];
}

// Role 기준: Core/Distribution/Floor 스위치는 네트워크의 "뼈대"라 기본적으로
// 펼쳐서 보여줘야 계층 구조(설계서가 요구한 Auvik류 하이라키)가 첫 화면부터
// 보인다. Device Type 기준: 라우터/스위치 계열(관리 인프라)은 펼치고, 대수가
// 많은 단말류(PC/카메라/AP 등)는 접어 둔다.
export const DEFAULT_EXPANDED_GROUPS: Record<GroupBy, string[]> = {
  ROLE: ["CORE_SWITCH", "DISTRIBUTION_SWITCH", "FLOOR_SWITCH"],
  DEVICE_TYPE: ["ROUTER", "L2_SWITCH", "L2_POE_SWITCH", "L3_SWITCH", "L3_POE_SWITCH"],
};

export function buildDrilldownGraph(
  topology: Topology,
  expandedGroups: Set<string>,
  groupBy: GroupBy,
): { nodes: DisplayNode[]; edges: DisplayEdge[] } {
  const displayIdOf = new Map<number, string>();
  const nodes: DisplayNode[] = [];
  const cloudAgg = new Map<string, { count: number; online: number; memberIds: number[] }>();

  for (const n of topology.nodes) {
    const group = groupKeyOf(n, groupBy);
    if (expandedGroups.has(group)) {
      const id = `device:${n.id}`;
      displayIdOf.set(n.id, id);
      nodes.push({ kind: "device", id, deviceId: n.id, node: n });
    } else {
      const cloudId = `cloud:${group}`;
      displayIdOf.set(n.id, cloudId);
      const agg = cloudAgg.get(group) ?? { count: 0, online: 0, memberIds: [] };
      agg.count += 1;
      if (n.status === "ONLINE") agg.online += 1;
      agg.memberIds.push(n.id);
      cloudAgg.set(group, agg);
    }
  }
  for (const [group, agg] of cloudAgg) {
    nodes.push({ kind: "cloud", id: `cloud:${group}`, group, count: agg.count, onlineCount: agg.online, memberIds: agg.memberIds });
  }

  const edgeMap = new Map<string, DisplayEdge>();
  for (const l of topology.links) {
    const a = displayIdOf.get(l.src_device_id);
    const b = displayIdOf.get(l.dst_device_id);
    if (!a || !b || a === b) continue; // 같은 구름 내부 연결은 구름 하나로 흡수한다.
    const [x, y] = a < b ? [a, b] : [b, a];
    const key = `${x}--${y}`;
    const entry = edgeMap.get(key) ?? { id: key, sourceId: x, targetId: y, count: 0, links: [] };
    entry.count += 1;
    entry.links.push(l);
    edgeMap.set(key, entry);
  }

  return { nodes, edges: Array.from(edgeMap.values()) };
}
