import { Topology, TopologyLink, TopologyNode } from "../api/client";

// 17.4절: "Endpoint가 많은 경우 유형별 Cluster(Camera × 35, PC × 42)로 묶고 확대 시
// 개별 노드로 전환한다"는 설계 원칙을 실제로 구현한다. ENDPOINT/SERVER/UNKNOWN처럼
// 대량으로 늘어나기 쉬운 역할만 클러스터 대상으로 삼고, Core/Distribution/Floor/
// Access 등 인프라 장비는 항상 개별로 표시한다.
export const CLUSTER_THRESHOLD = 4;
const CLUSTERABLE_ROLES = new Set(["ENDPOINT", "SERVER", "UNKNOWN"]);

export type RenderNode = TopologyNode & {
  cluster_key?: string;
  cluster_count?: number;
};

export interface ClusteredTopology {
  nodes: RenderNode[];
  links: TopologyLink[];
  clusterKeys: string[]; // 이번 계산에서 실제로 접힌 클러스터 key 목록 (모두 펼치기용)
}

function findParentId(nodeId: number, nodesById: Map<number, TopologyNode>, links: TopologyLink[]): number | null {
  for (const link of links) {
    let otherId: number | null = null;
    if (link.src_device_id === nodeId) otherId = link.dst_device_id;
    else if (link.dst_device_id === nodeId) otherId = link.src_device_id;
    if (otherId == null) continue;
    const other = nodesById.get(otherId);
    if (other && !CLUSTERABLE_ROLES.has(other.device_role)) {
      return otherId;
    }
  }
  return null;
}

export function buildClusteredTopology(
  topology: Topology,
  expandedKeys: Set<string>,
  threshold: number = CLUSTER_THRESHOLD,
): ClusteredTopology {
  const nodesById = new Map(topology.nodes.map((n) => [n.id, n]));

  const groups = new Map<string, { parentId: number; deviceType: string; ids: number[] }>();
  const passthroughIds = new Set<number>();

  for (const node of topology.nodes) {
    if (!CLUSTERABLE_ROLES.has(node.device_role)) {
      passthroughIds.add(node.id);
      continue;
    }
    const parentId = findParentId(node.id, nodesById, topology.links);
    if (parentId == null) {
      passthroughIds.add(node.id); // 상위 장비를 특정할 수 없으면 묶지 않고 그대로 표시
      continue;
    }
    const key = `${parentId}:${node.device_type}`;
    const group = groups.get(key) ?? { parentId, deviceType: node.device_type, ids: [] };
    group.ids.push(node.id);
    groups.set(key, group);
  }

  const clusterKeys: string[] = [];
  const clusterNodes: RenderNode[] = [];
  const clusterLinks: TopologyLink[] = [];
  let syntheticId = -1;

  for (const [key, group] of groups) {
    if (group.ids.length > threshold && !expandedKeys.has(key)) {
      clusterKeys.push(key);
      const id = syntheticId--;
      const parentDepth = nodesById.get(group.parentId)?.discovery_depth ?? 0;
      clusterNodes.push({
        id,
        hostname: `${group.deviceType} × ${group.ids.length}`,
        sys_name: null,
        management_ip: null,
        sys_descr: null,
        primary_mac: null,
        device_type: group.deviceType,
        device_role: "CLUSTER",
        status: "ONLINE",
        discovery_depth: parentDepth + 1,
        is_stp_root: false,
        cluster_key: key,
        cluster_count: group.ids.length,
      });
      clusterLinks.push({
        id: syntheticId--,
        src_device_id: group.parentId,
        src_interface_id: null,
        dst_device_id: id,
        dst_interface_id: null,
        source: "CLUSTER",
        label: null,
        confidence: 100,
        status: "UP",
        first_seen_at: new Date(0).toISOString(),
        last_seen_at: new Date(0).toISOString(),
        src_port: null,
        dst_port: null,
        src_stp_state: null,
        dst_stp_state: null,
        // Cluster는 항상 ENDPOINT/SERVER/UNKNOWN 자식을 상위 장비 밑으로 묶은
        // 것이므로 계층 간 연결(HIERARCHICAL)로 취급한다.
        link_role: "HIERARCHICAL",
      });
    } else {
      group.ids.forEach((memberId) => passthroughIds.add(memberId));
    }
  }

  const nodes: RenderNode[] = [...topology.nodes.filter((n) => passthroughIds.has(n.id)), ...clusterNodes];
  const links = [
    ...topology.links.filter((l) => passthroughIds.has(l.src_device_id) && passthroughIds.has(l.dst_device_id)),
    ...clusterLinks,
  ];

  return { nodes, links, clusterKeys };
}
