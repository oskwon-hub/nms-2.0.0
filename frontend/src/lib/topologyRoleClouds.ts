import { Topology, TopologyLink } from "../api/client";

// [KOS20260922] "Topology 화면은 Device 구분을 구름으로 표시하고 관계를 표시" 요청 -
// 개별 장비 수백 개를 한 화면에 그리는 대신, device_role(Core/Distribution/Floor/
// Access/Edge/Server/Endpoint/Unknown) 단위로 항상 하나의 "구름"으로 묶어 전체
// 네트워크의 논리적 구조만 먼저 보여준다. 구름을 클릭하면 그 안의 실제 장비들을
// 별도 화면(팝업)으로 확인한다 - Core/Distribution/Floor는 하위 스위치 +
// ARP 기반 약한 링크까지 함께(filterTopologyByRole), Access는 Role 내부만
// 보여주되 개별 장비를 클릭하면 그 장비의 하위 노드만 또 다른 팝업으로
// (filterTopologyByDeviceChildren).
export interface RoleCloudNode {
  role: string;
  count: number;
  onlineCount: number;
  memberIds: number[];
}

export interface RoleCloudEdge {
  id: string;
  srcRole: string;
  dstRole: string;
  count: number;
}

// Role들 사이의 상하 관계(위→아래) 표시 순서. 분류 로직에 없는 값이 들어와도
// (예: 향후 Role 추가) 깨지지 않도록 목록에 없으면 맨 아래로 보낸다.
const ROLE_ORDER = [
  "CORE_SWITCH",
  "DISTRIBUTION_SWITCH",
  "FLOOR_SWITCH",
  "ACCESS_SWITCH",
  "EDGE_DEVICE",
  "SERVER",
  "ENDPOINT",
  "UNKNOWN",
];

function roleRank(role: string): number {
  const idx = ROLE_ORDER.indexOf(role);
  return idx === -1 ? ROLE_ORDER.length : idx;
}

// [KOS20260922] "CORE_SWITCH, DISTRIBUTION_SWITCH, FLOOR_SWITCH는 팝업에서 하위
// 스위치를 보여 줘" 요청 - 이 세 백본 계층은 팝업에서 같은 Role끼리만 보여주면
// 계층 구조(Core -> Distribution -> Floor)가 안 보인다. 이 Role들만 한 단계
// 아래 스위치까지 함께 포함한다.
//
// "하위"를 판단할 때 discovery_depth를 쓰지 않는다 - 이 필드는 BFS로 이웃을
// 재귀 확장할 때만 채워지는데(discovery/engine.py _collect_neighbor_ips),
// CIDR을 직접 넘겨 스캔하면 모든 장비가 seed로 잡혀 depth=0인 채로 남는다.
// 실사용 데이터에서 실제로 전 장비가 depth=0이라 depth 비교로는 "하위"를 전혀
// 가려낼 수 없었다(회귀 재현: 172.16.x 실 데이터로 직접 확인). 대신 이미
// 신뢰할 수 있게 채워지는 device_role의 계층 순서(ROLE_ORDER)로 판단한다 -
// buildRoleCloudGraph()가 구름 사이 관계 방향을 정할 때와 동일한 기준.
//
// ACCESS_SWITCH는 (그 아래는 보통 스위치가 아니라 단말이라) 기존처럼 Role
// 내부 Peer 관계만 보여주고, 대신 특정 장비를 클릭했을 때 그 장비의 하위
// 노드만 별도로 다시 조회하는 filterTopologyByDeviceChildren()을 쓴다.
const BACKBONE_SWITCH_ROLES = new Set(["CORE_SWITCH", "DISTRIBUTION_SWITCH", "FLOOR_SWITCH"]);
const SWITCH_ROLES = new Set(["CORE_SWITCH", "DISTRIBUTION_SWITCH", "FLOOR_SWITCH", "ACCESS_SWITCH"]);

export function buildRoleCloudGraph(topology: Topology): { nodes: RoleCloudNode[]; edges: RoleCloudEdge[] } {
  const byRole = new Map<string, RoleCloudNode>();
  for (const node of topology.nodes) {
    const entry = byRole.get(node.device_role) ?? { role: node.device_role, count: 0, onlineCount: 0, memberIds: [] };
    entry.count += 1;
    if (node.status === "ONLINE") entry.onlineCount += 1;
    entry.memberIds.push(node.id);
    byRole.set(node.device_role, entry);
  }

  const nodesById = new Map(topology.nodes.map((n) => [n.id, n]));
  const edgeCounts = new Map<string, RoleCloudEdge>();
  for (const link of topology.links) {
    const src = nodesById.get(link.src_device_id);
    const dst = nodesById.get(link.dst_device_id);
    // 같은 Role끼리의 연결(예: Core 이중화 Peer)은 구름 사이 관계가 아니라 그
    // 구름 내부 관계이므로, 여기서는 만들지 않고 filterTopologyByRole()로 연
    // 팝업에서만 보여준다.
    if (!src || !dst || src.device_role === dst.device_role) continue;
    const [a, b] =
      roleRank(src.device_role) <= roleRank(dst.device_role)
        ? [src.device_role, dst.device_role]
        : [dst.device_role, src.device_role];
    const key = `${a}--${b}`;
    const entry = edgeCounts.get(key) ?? { id: key, srcRole: a, dstRole: b, count: 0 };
    entry.count += 1;
    edgeCounts.set(key, entry);
  }

  const nodes = Array.from(byRole.values()).sort((a, b) => roleRank(a.role) - roleRank(b.role));
  const edges = Array.from(edgeCounts.values());
  return { nodes, edges };
}

// 구름 클릭 시 팝업에 넘길 부분 Topology - 그 Role에 속한 장비와, 그 장비들
// "끼리만"의 링크(동일 Role 내부 Peer 관계)를 기본으로 남긴다. CORE/DISTRIBUTION/
// FLOOR_SWITCH는 한 단계 아래 스위치(하위 스위치)까지 함께 포함해, 팝업 안에서
// 백본 계층 구조가 보이게 한다.
//
// [KOS20260922] "백본 팝업에 하위 스위치뿐 아니라 ARP 기반 약한 링크도 같이
// 보여주도록 필터를 완화" 요청 - 운영 환경의 한 DISTRIBUTION_SWITCH를 조사하며,
// LLDP/FDB 근거가 전혀 없어 실제로는 ARP 기반 약한 링크(source: "ARP") 2개가
// 있는데도 하위 스위치 필터에 안 걸려 팝업에서 "링크 0개"로 보였다. LLDP/FDB
// 없는 장비가 드물지 않으므로, 하위 스위치 조건과 별개로 Role과 무관하게
// source가 "ARP"인 링크는 항상 포함한다(LLDP/FDB보다 신뢰도가 낮은 근거이지만
// "그나마 있는 근거"를 숨기지 않는다는 취지).
export function filterTopologyByRole(topology: Topology, role: string): Topology {
  const members = topology.nodes.filter((n) => n.device_role === role);
  const memberIds = new Set(members.map((n) => n.id));

  if (!BACKBONE_SWITCH_ROLES.has(role)) {
    return {
      nodes: topology.nodes.filter((n) => memberIds.has(n.id)),
      links: topology.links.filter((l) => memberIds.has(l.src_device_id) && memberIds.has(l.dst_device_id)),
    };
  }

  const memberRank = roleRank(role);
  const nodesById = new Map(topology.nodes.map((n) => [n.id, n]));
  const extraIds = new Set<number>();
  const relevantLinks: TopologyLink[] = [];
  for (const link of topology.links) {
    const src = nodesById.get(link.src_device_id);
    const dst = nodesById.get(link.dst_device_id);
    if (!src || !dst) continue;
    const srcIsMember = memberIds.has(src.id);
    const dstIsMember = memberIds.has(dst.id);
    if (!srcIsMember && !dstIsMember) continue;
    if (srcIsMember && dstIsMember) {
      relevantLinks.push(link); // 동일 Role 내부(Peer/이중화) 링크
      continue;
    }
    const other = srcIsMember ? dst : src;
    const isChildSwitch = SWITCH_ROLES.has(other.device_role) && roleRank(other.device_role) > memberRank;
    const isWeakArpLink = link.source === "ARP";
    if (isChildSwitch || isWeakArpLink) {
      extraIds.add(other.id);
      relevantLinks.push(link);
    }
  }

  const allIds = new Set<number>([...memberIds, ...extraIds]);
  return {
    nodes: topology.nodes.filter((n) => allIds.has(n.id)),
    links: relevantLinks,
  };
}

// [KOS20260922] "ACCESS_SWITCH는 노드를 클릭하면 ... 팝업으로 하위 노드를 다시
// 조회해서 그래프로 보여 줘" 요청 - 특정 장비 하나를 기준으로 그 장비보다
// device_role 계층이 더 아래(ROLE_ORDER 기준 roleRank가 더 큰)인 이웃들만
// 모아 별도 팝업에 띄울 부분 Topology를 만든다(discovery_depth를 쓰지 않는
// 이유는 filterTopologyByRole()의 주석 참고 - 실사용 데이터에서 항상 0이라
// 하위 판정에 쓸 수 없었다). 이 판정 기준을 쓰면 같은 Role(Peer, 예: 다른
// ACCESS_SWITCH)이나 더 상위 Role(예: CORE_SWITCH)은 자연히 제외된다.
export function filterTopologyByDeviceChildren(topology: Topology, deviceId: number): Topology {
  const root = topology.nodes.find((n) => n.id === deviceId);
  if (!root) return { nodes: [], links: [] };
  const rootRank = roleRank(root.device_role);

  const nodesById = new Map(topology.nodes.map((n) => [n.id, n]));
  const childIds = new Set<number>();
  const childLinks: TopologyLink[] = [];
  for (const link of topology.links) {
    let otherId: number | null = null;
    if (link.src_device_id === deviceId) otherId = link.dst_device_id;
    else if (link.dst_device_id === deviceId) otherId = link.src_device_id;
    if (otherId == null) continue;
    const other = nodesById.get(otherId);
    if (!other || roleRank(other.device_role) <= rootRank) continue; // 상위/Peer는 제외, 하위만
    childIds.add(otherId);
    childLinks.push(link);
  }

  return {
    nodes: topology.nodes.filter((n) => n.id === deviceId || childIds.has(n.id)),
    links: childLinks,
  };
}

// [KOS20260922] "전체 보기에서 노드를 클릭하면 자식, 그 자식, 단말 노드까지
// 전체를 보여 주는 팝업" 요청 - filterTopologyByDeviceChildren()은 한 단계
// 아래 이웃만 포함해 손자 이하는 팝업에 나타나지 않는다. 이 함수는 "지금 보고
// 있는 노드"의 rank를 기준으로(root 기준 고정이 아니라) rank가 더 큰 이웃으로
// 계속 재귀적으로 내려가, Core->Distribution->Floor->Access->Endpoint처럼
// 여러 계층을 거쳐 최종 단말까지 전체 하위 트리를 모은다.
export function filterTopologyByDeviceDescendants(topology: Topology, deviceId: number): Topology {
  const root = topology.nodes.find((n) => n.id === deviceId);
  if (!root) return { nodes: [], links: [] };

  const nodesById = new Map(topology.nodes.map((n) => [n.id, n]));
  const linksByNode = new Map<number, TopologyLink[]>();
  for (const link of topology.links) {
    for (const id of [link.src_device_id, link.dst_device_id]) {
      if (!linksByNode.has(id)) linksByNode.set(id, []);
      linksByNode.get(id)!.push(link);
    }
  }

  const includedIds = new Set<number>([deviceId]);
  const queue: number[] = [deviceId];
  while (queue.length > 0) {
    const currentId = queue.shift()!;
    const current = nodesById.get(currentId);
    if (!current) continue;
    const currentRank = roleRank(current.device_role);
    for (const link of linksByNode.get(currentId) ?? []) {
      const otherId = link.src_device_id === currentId ? link.dst_device_id : link.src_device_id;
      if (includedIds.has(otherId)) continue;
      const other = nodesById.get(otherId);
      if (!other || roleRank(other.device_role) <= currentRank) continue; // 상위/Peer는 제외, 하위만
      includedIds.add(otherId);
      queue.push(otherId);
    }
  }

  return {
    nodes: topology.nodes.filter((n) => includedIds.has(n.id)),
    links: topology.links.filter((l) => includedIds.has(l.src_device_id) && includedIds.has(l.dst_device_id)),
  };
}
