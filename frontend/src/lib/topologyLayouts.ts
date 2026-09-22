import { forceCenter, forceCollide, forceLink, forceManyBody, forceSimulation } from "d3-force";
import { Topology, TopologyNode } from "../api/client";

export type LayoutMode = "auto" | "circle" | "grid" | "tree-horizontal" | "tree-vertical";

export const LAYOUT_LABELS: Record<LayoutMode, string> = {
  auto: "자동 배치",
  circle: "원 배치",
  grid: "격자 배치",
  "tree-horizontal": "수평 트리",
  "tree-vertical": "수직 트리",
};

// 17.4절: Core -> Distribution -> Floor -> Access -> Endpoint 흐름을 트리 배치의
// 기준 축으로 사용한다.
const ROLE_TIER: Record<string, number> = {
  CORE_SWITCH: 0,
  DISTRIBUTION_SWITCH: 1,
  FLOOR_SWITCH: 2,
  ACCESS_SWITCH: 3,
  EDGE_DEVICE: 4,
  SERVER: 4,
  ENDPOINT: 5,
  CLUSTER: 5,
  UNKNOWN: 6,
};

const TIER_GAP = 150;
const SIBLING_GAP = 190;

export type NodePosition = { x: number; y: number };

// [KOS20260922] "선이 꼬이지 않도록, 한 노드에서 여러 노드로 연결될 때 꼬이지
// 않도록 배치" 요청 - 링크를 무시하고 입력 순서 그대로 인접 목록/좌표를 매기면,
// 같은 부모의 자식들이 서로 멀리 떨어져 배치될 수 있어 부모-자식 선끼리
// 불필요하게 교차한다. src/dst 양쪽 방향 인접 목록을 만들어 두면 tree/circle
// 배치 모두 "이미 배치된 이웃 근처에 놓기" 휴리스틱에 재사용할 수 있다.
function buildNeighborMap(links: Topology["links"]): Map<number, number[]> {
  const neighbors = new Map<number, number[]>();
  for (const l of links) {
    if (!neighbors.has(l.src_device_id)) neighbors.set(l.src_device_id, []);
    if (!neighbors.has(l.dst_device_id)) neighbors.set(l.dst_device_id, []);
    neighbors.get(l.src_device_id)!.push(l.dst_device_id);
    neighbors.get(l.dst_device_id)!.push(l.src_device_id);
  }
  return neighbors;
}

// Sugiyama 스타일 계층 그래프 배치의 "Barycenter" 휴리스틱을 단순화해 적용한다:
// 위(상위 Tier)에서 아래로 내려가며, 이미 배치된 상위 이웃들의 평균 column
// 위치를 기준으로 같은 Tier의 형제 노드 순서를 정렬한다. 같은 부모의 자식들이
// 부모 근처(비슷한 column)로 모이므로, 서로 다른 부모의 자식 그룹끼리 선이
// 겹쳐 지나가는 일이 크게 줄어든다.
function layoutTree(nodes: TopologyNode[], links: Topology["links"], horizontal: boolean): Map<number, NodePosition> {
  const tierOf = new Map<number, number>();
  for (const n of nodes) tierOf.set(n.id, ROLE_TIER[n.device_role] ?? 6);

  const tiers = new Map<number, number[]>();
  for (const n of nodes) {
    const tier = tierOf.get(n.id)!;
    if (!tiers.has(tier)) tiers.set(tier, []);
    tiers.get(tier)!.push(n.id);
  }
  const sortedTiers = Array.from(tiers.keys()).sort((a, b) => a - b);
  const neighbors = buildNeighborMap(links);
  const columnOf = new Map<number, number>();

  for (const tier of sortedTiers) {
    const ids = tiers.get(tier)!;
    const withBarycenter = ids.map((id, idx) => {
      const knownCols = (neighbors.get(id) ?? [])
        .map((n) => columnOf.get(n))
        .filter((c): c is number => c != null);
      // 아직 배치된 이웃이 없으면(최상위 Tier이거나 고립 노드) 원래 순서를 그대로 쓴다.
      const barycenter = knownCols.length > 0 ? knownCols.reduce((a, b) => a + b, 0) / knownCols.length : idx;
      return { id, barycenter, idx };
    });
    // barycenter가 같으면 원래 순서를 유지해(idx 보조 정렬) 매번 같은 배치가 나오게 한다.
    withBarycenter.sort((a, b) => a.barycenter - b.barycenter || a.idx - b.idx);
    withBarycenter.forEach((item, col) => columnOf.set(item.id, col));
  }

  const positions = new Map<number, NodePosition>();
  for (const n of nodes) {
    const tier = tierOf.get(n.id)!;
    const col = columnOf.get(n.id) ?? 0;
    positions.set(n.id, horizontal ? { x: tier * TIER_GAP + 100, y: col * SIBLING_GAP } : { x: col * SIBLING_GAP, y: tier * TIER_GAP });
  }
  return positions;
}

// 원 배치도 입력 순서 그대로 각도를 매기면 서로 연결된 노드가 원 반대편에
// 떨어져 선이 원 내부를 가로지르며 꼬인다. 링크를 따라가는 BFS 순서로 각도를
// 매기면 연결된 노드끼리 원 위에서도 이웃하게 되어 교차가 크게 줄어든다.
function layoutCircle(nodes: TopologyNode[], links: Topology["links"]): Map<number, NodePosition> {
  const positions = new Map<number, NodePosition>();
  const count = Math.max(nodes.length, 1);
  const radius = Math.max(150, count * 28);
  const neighbors = buildNeighborMap(links);
  const idSet = new Set(nodes.map((n) => n.id));

  const order: number[] = [];
  const visited = new Set<number>();
  for (const start of nodes) {
    if (visited.has(start.id)) continue;
    const queue = [start.id];
    visited.add(start.id);
    while (queue.length > 0) {
      const current = queue.shift()!;
      order.push(current);
      for (const next of neighbors.get(current) ?? []) {
        if (!idSet.has(next) || visited.has(next)) continue;
        visited.add(next);
        queue.push(next);
      }
    }
  }

  order.forEach((id, index) => {
    const angle = (2 * Math.PI * index) / count;
    positions.set(id, { x: radius * Math.cos(angle) + radius, y: radius * Math.sin(angle) + radius });
  });
  return positions;
}

function layoutGrid(nodes: TopologyNode[]): Map<number, NodePosition> {
  const positions = new Map<number, NodePosition>();
  const columns = Math.max(1, Math.ceil(Math.sqrt(nodes.length)));
  nodes.forEach((n, index) => {
    const row = Math.floor(index / columns);
    const col = index % columns;
    positions.set(n.id, { x: col * 170, y: row * 150 });
  });
  return positions;
}

// [KOS20260922] "구름보기/전체보기/자동 배치 시 노드가 겹치지 않도록" 요청 -
// forceCollide(60)은 실제 렌더링 크기보다 작았다. 일반 장비 노드는 ReactFlow
// 기본 폭(약 150px)에 긴 hostname 라벨까지 더해지고, 구름 노드(RoleCloudNode)는
// 160x100 SVG라 훨씬 크다 - 60px 반경으로는 둘 다 겹칠 수 있었다. 호출하는
// 쪽(장비 그래프 vs 구름 개요)이 실제 노드 크기에 맞는 반경을 넘길 수 있도록
// 매개변수화하고, 기본값도 늘렸다.
const DEFAULT_AUTO_LAYOUT_NODE_RADIUS = 90;

function layoutAuto(
  nodes: TopologyNode[],
  links: Topology["links"],
  nodeRadius: number = DEFAULT_AUTO_LAYOUT_NODE_RADIUS,
): Map<number, NodePosition> {
  type SimNode = { id: number; x?: number; y?: number };
  const simNodes: SimNode[] = nodes.map((n) => ({ id: n.id }));
  const simLinks = links.map((l) => ({ source: l.src_device_id, target: l.dst_device_id }));

  const simulation = forceSimulation(simNodes as any)
    .force("charge", forceManyBody().strength(-260))
    .force("link", forceLink(simLinks as any).id((d: any) => d.id).distance(120))
    .force("center", forceCenter(300, 250))
    .force("collide", forceCollide(nodeRadius))
    .stop();

  // 애니메이션 없이 한 번에 안정된 배치를 계산한다 (충분한 반복 후 정지).
  // [KOS20260922] forceCollide는 반복이 유한하면 목표 간격(2*반경)에 점근적으로만
  // 수렴한다 - 300회로는 노드가 많을 때(예: 100개) 목표보다 최대 몇 px 덜 떨어진
  // 채로 멈춰 미세하게 겹칠 수 있었다(직접 시뮬레이션해 확인: 300회=4.5px 부족,
  // 500회=0.04px 부족). 500회로 늘려 사실상 완전히 수렴하게 한다.
  for (let i = 0; i < 500; i += 1) {
    simulation.tick();
  }

  const positions = new Map<number, NodePosition>();
  for (const n of simNodes) {
    positions.set(n.id, { x: n.x ?? 0, y: n.y ?? 0 });
  }
  return positions;
}

export function computeLayoutPositions(
  topology: Topology,
  mode: LayoutMode,
  options?: { autoLayoutNodeRadius?: number },
): Map<number, NodePosition> {
  switch (mode) {
    case "circle":
      return layoutCircle(topology.nodes, topology.links);
    case "grid":
      return layoutGrid(topology.nodes);
    case "tree-horizontal":
      return layoutTree(topology.nodes, topology.links, true);
    case "tree-vertical":
      return layoutTree(topology.nodes, topology.links, false);
    case "auto":
    default:
      return layoutAuto(topology.nodes, topology.links, options?.autoLayoutNodeRadius);
  }
}
