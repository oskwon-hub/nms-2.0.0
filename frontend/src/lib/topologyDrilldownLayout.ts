import { forceCenter, forceCollide, forceLink, forceManyBody, forceSimulation } from "d3-force";
import { DisplayEdge, DisplayNode, GroupBy, groupKeyOf } from "./topologyDrilldown";

// [KOS20260923] "그래프가 왜 Topology와 같은 형태냐 - 이미지로 준 건 다른
// 형태다" 지적 - 최초 구현은 기존 topologyLayouts.ts의 tree/circle/auto를 그대로
// 재사용해, 3가지 "스타일"이 실제로는 배경색만 다르고 모양은 기존 Topology와
// 동일했다. 참고 스크린샷(계층형 다이어그램/방사형 Org Map/다크 트래픽 맵)의
// 실제 배치 모양을 재현하려면 계층형(행)과 방사형(동심원)이 근본적으로 다른
// 좌표 계산이 필요해, drilldown 표시 그래프(DisplayNode/DisplayEdge) 전용
// 레이아웃을 새로 만든다(기존 topologyLayouts.ts는 수정하지 않음).
export type DrilldownLayoutMode = "hierarchical" | "radial" | "force";

const ROLE_TIER: Record<string, number> = {
  CORE_SWITCH: 0,
  DISTRIBUTION_SWITCH: 1,
  FLOOR_SWITCH: 2,
  ACCESS_SWITCH: 3,
  EDGE_DEVICE: 4,
  SERVER: 4,
  ENDPOINT: 5,
  UNKNOWN: 6,
};

// [KOS20260923] "Device Type별로도 볼 수 있도록" 요청 - Role 계층과는 다른
// 축(장비 종류)이라 별도 Tier 표를 둔다: 라우터/L3/L2 스위치는 관리망 뼈대라
// 위쪽에, 단말류(카메라/PC)는 아래쪽에 오도록 8장(device_type.py) 분류값
// 기준으로 배치한다.
const DEVICE_TYPE_TIER: Record<string, number> = {
  ROUTER: 0,
  L3_SWITCH: 1,
  L3_POE_SWITCH: 1,
  L2_SWITCH: 2,
  L2_POE_SWITCH: 2,
  ACCESS_POINT: 3,
  HUB: 3,
  IP_CAMERA: 4,
  WINDOWS_PC: 4,
  LINUX_PC: 4,
  MAC_PC: 4,
  UNKNOWN: 5,
};

function tierOf(n: DisplayNode, groupBy: GroupBy): number {
  const group = n.kind === "device" ? groupKeyOf(n.node, groupBy) : n.group;
  const tierMap = groupBy === "DEVICE_TYPE" ? DEVICE_TYPE_TIER : ROLE_TIER;
  return tierMap[group] ?? 6;
}

function buildNeighborMap(edges: DisplayEdge[]): Map<string, string[]> {
  const neighbors = new Map<string, string[]>();
  for (const e of edges) {
    if (!neighbors.has(e.sourceId)) neighbors.set(e.sourceId, []);
    if (!neighbors.has(e.targetId)) neighbors.set(e.targetId, []);
    neighbors.get(e.sourceId)!.push(e.targetId);
    neighbors.get(e.targetId)!.push(e.sourceId);
  }
  return neighbors;
}

// 같은 Tier 안에서 형제 순서를 "이미 배치된 상위 이웃들의 평균 위치"에 맞춰
// 정렬하는 Barycenter 휴리스틱 - topologyLayouts.ts의 layoutTree()와 같은
// 아이디어를 DisplayNode 문자열 id 기준 범용으로 다시 구현한다(대상 그래프
// 형태가 달라 그대로 재사용할 수 없음).
function assignColumns(tiers: Map<number, string[]>, sortedTiers: number[], neighbors: Map<string, string[]>): Map<string, number> {
  const columnOf = new Map<string, number>();
  for (const tier of sortedTiers) {
    const ids = tiers.get(tier)!;
    const withBarycenter = ids.map((id, idx) => {
      const known = (neighbors.get(id) ?? []).map((n) => columnOf.get(n)).filter((c): c is number => c != null);
      const barycenter = known.length > 0 ? known.reduce((a, b) => a + b, 0) / known.length : idx;
      return { id, barycenter, idx };
    });
    withBarycenter.sort((a, b) => a.barycenter - b.barycenter || a.idx - b.idx);
    withBarycenter.forEach((item, col) => columnOf.set(item.id, col));
  }
  return columnOf;
}

function groupByTier(
  nodes: DisplayNode[],
  groupBy: GroupBy,
): { tierOfId: Map<string, number>; tiers: Map<number, string[]>; sortedTiers: number[] } {
  const tierOfId = new Map<string, number>();
  const tiers = new Map<number, string[]>();
  for (const n of nodes) {
    const t = tierOf(n, groupBy);
    tierOfId.set(n.id, t);
    if (!tiers.has(t)) tiers.set(t, []);
    tiers.get(t)!.push(n.id);
  }
  const sortedTiers = Array.from(tiers.keys()).sort((a, b) => a - b);
  return { tierOfId, tiers, sortedTiers };
}

const TIER_GAP = 170;
const SIBLING_GAP = 210;

// Style 1 - "Auvik류" 계층형: 위에서 아래로 Tier(행)를 쌓는 배치. 기존
// tree-vertical과 개념은 같지만 DisplayNode(구름 포함) 기준으로 다시 계산한다.
function layoutHierarchical(nodes: DisplayNode[], edges: DisplayEdge[], groupBy: GroupBy): Map<string, { x: number; y: number }> {
  const { tierOfId, tiers, sortedTiers } = groupByTier(nodes, groupBy);
  const neighbors = buildNeighborMap(edges);
  const columnOf = assignColumns(tiers, sortedTiers, neighbors);
  const positions = new Map<string, { x: number; y: number }>();
  for (const n of nodes) {
    const tier = tierOfId.get(n.id)!;
    const col = columnOf.get(n.id) ?? 0;
    positions.set(n.id, { x: col * SIBLING_GAP, y: tier * TIER_GAP });
  }
  return positions;
}

const RING_GAP = 190;

// Style 2 - "ManageEngine류" 방사형 Org Map: Tier를 동심원의 반지름으로 쓰고,
// 같은 Tier 안에서는 Barycenter로 구한 순서를 각도로 균등 배분한다. 부모 근처의
// 자식들이 비슷한 각도에 모이므로 부모-자식 선이 원 반대편으로 가로지르지 않는다.
function layoutRadial(nodes: DisplayNode[], edges: DisplayEdge[], groupBy: GroupBy): Map<string, { x: number; y: number }> {
  const { tierOfId, tiers, sortedTiers } = groupByTier(nodes, groupBy);
  const neighbors = buildNeighborMap(edges);
  const columnOf = assignColumns(tiers, sortedTiers, neighbors);
  const positions = new Map<string, { x: number; y: number }>();
  for (const tier of sortedTiers) {
    const ids = tiers.get(tier)!;
    const radius = tier * RING_GAP;
    if (tier === 0 && ids.length === 1) {
      positions.set(ids[0], { x: 0, y: 0 });
      continue;
    }
    const count = ids.length;
    for (const id of ids) {
      const col = columnOf.get(id) ?? 0;
      const angle = (2 * Math.PI * col) / count;
      positions.set(id, { x: radius * Math.cos(angle), y: radius * Math.sin(angle) });
    }
  }
  for (const n of nodes) {
    const t = tierOfId.get(n.id)!;
    if (!positions.has(n.id)) positions.set(n.id, { x: 0, y: t * RING_GAP });
  }
  return positions;
}

// Style 3 - 다크 트래픽 뷰: force-directed. drilldown으로 이미 노드 수가 줄어든
// 뒤에 적용되므로(기본 화면 기준 수십 개) 비용 문제 없이 매번 다시 계산해도 된다.
function layoutForce(nodes: DisplayNode[], edges: DisplayEdge[]): Map<string, { x: number; y: number }> {
  type SimNode = { id: string; x?: number; y?: number };
  const simNodes: SimNode[] = nodes.map((n) => ({ id: n.id }));
  const simLinks = edges.map((e) => ({ source: e.sourceId, target: e.targetId }));

  const simulation = forceSimulation(simNodes as any)
    .force("charge", forceManyBody().strength(-320))
    .force("link", forceLink(simLinks as any).id((d: any) => d.id).distance(140))
    .force("center", forceCenter(0, 0))
    .force("collide", forceCollide(90))
    .stop();
  for (let i = 0; i < 400; i += 1) simulation.tick();

  const positions = new Map<string, { x: number; y: number }>();
  for (const n of simNodes) positions.set(n.id, { x: n.x ?? 0, y: n.y ?? 0 });
  return positions;
}

export function computeDrilldownLayout(
  nodes: DisplayNode[],
  edges: DisplayEdge[],
  mode: DrilldownLayoutMode,
  groupBy: GroupBy,
): Map<string, { x: number; y: number }> {
  if (mode === "radial") return layoutRadial(nodes, edges, groupBy);
  if (mode === "force") return layoutForce(nodes, edges);
  return layoutHierarchical(nodes, edges, groupBy);
}
