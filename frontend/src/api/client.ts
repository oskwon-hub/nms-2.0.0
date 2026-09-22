// 16장 REST API에 대응하는 타입/호출 함수 모음.

export interface Device {
  id: number;
  hostname: string | null;
  management_ip: string | null;
  primary_mac: string | null;
  vendor: string | null;
  model: string | null;
  serial_number: string | null;
  device_type: string;
  device_role: string;
  role_source: "AUTO" | "MANUAL";
  classification_score: number;
  role_score: number;
  status: "ONLINE" | "STALE" | "OFFLINE" | "UNKNOWN";
  physical_floor: string | null;
  discovery_depth: number;
  layer2_capable: boolean;
  layer3_capable: boolean;
  poe_capable: boolean;
  // [KOS20260922] BRIDGE-MIB dot1dStpRootPort==0으로 확인된 STP Root Bridge 여부.
  is_stp_root: boolean;
  first_seen_at: string;
  last_seen_at: string;
}

export interface DeviceDetail extends Device {
  sys_location: string | null;
  sys_descr: string | null;
  classification_method: string | null;
  classification_detail: string | null;
  role_detail: string | null;
  floor_source: string | null;
}

export interface DeviceDiagnostic {
  target: string;
  source_ip: string | null;
  command: string;
  protocol: "TCP" | "UDP" | "ICMP";
  success: boolean;
  output: string;
  hops: {
    hop: number;
    ip: string | null;
    device_id: number | null;
    hostname: string | null;
    device_role: string | null;
  }[];
}

export interface L2SwitchEvidence {
  switch_id: number;
  hostname: string | null;
  sys_name: string | null;
  management_ip: string | null;
  sys_descr: string | null;
  device_type: string;
  vlan: number | null;
  source_ports: string[];
  target_ports: string[];
  source_seen_at: string | null;
  target_seen_at: string | null;
  relation: "ONE_SIDE" | "UNKNOWN_PORT" | "SAME_PORT" | "DIFFERENT_PORTS";
}

export interface L2PathEvidence {
  source_ip: string | null;
  source_mac: string | null;
  target_ip: string;
  target_mac: string | null;
  switches: L2SwitchEvidence[];
}

export interface DeviceInterfaceRow {
  id: number;
  device_id: number;
  if_index: number;
  name: string | null;
  mac: string | null;
  admin_status: string;
  oper_status: string;
  speed_mbps: number | null;
  vlan: number | null;
  is_trunk: boolean;
  is_uplink: boolean;
  is_downlink: boolean;
  is_protected: boolean;
  protected_reason: string | null;
  poe_capable: boolean;
  // [KOS20260921] 마지막 수집 시점의 누적 카운터(바이트) - 순간 전송률이 아니다.
  in_octets: number | null;
  out_octets: number | null;
  // [KOS20260922] BRIDGE-MIB dot1dStpPortState. STP 미지원/비대상 포트는 null.
  stp_state: string | null;
}

export interface NeighborRow {
  id: number;
  local_interface_id: number | null;
  remote_chassis_id: string | null;
  remote_port_id: string | null;
  remote_sys_name: string | null;
  remote_mgmt_ip: string | null;
  protocol: string;
  last_seen_at: string;
}

export interface FdbRow {
  id: number;
  interface_id: number | null;
  vlan: number | null;
  mac: string;
  entry_type: string;
  last_seen_at: string;
}

export interface ArpRow {
  id: number;
  interface_id: number | null;
  ip: string;
  mac: string;
  state: string;
  last_seen_at: string;
}

export interface RouteRow {
  id: number;
  destination: string;
  prefix_len: number;
  next_hop: string | null;
  interface_id: number | null;
  route_type: string;
}

export interface PoeRow {
  id: number;
  interface_id: number;
  enabled: boolean;
  status: string;
  power_mw: number | null;
  poe_class: string | null;
}

export interface TopologyNode {
  id: number;
  hostname: string | null;
  sys_name: string | null;
  management_ip: string | null;
  sys_descr: string | null;
  primary_mac: string | null;
  device_type: string;
  device_role: string;
  status: string;
  discovery_depth: number;
  // [KOS20260922] Topology 그래프에서 STP Root 노드를 시각적으로 표시하기 위해 노출한다.
  is_stp_root: boolean;
}

export interface TopologyLink {
  id: number;
  src_device_id: number;
  src_interface_id: number | null;
  dst_device_id: number;
  dst_interface_id: number | null;
  source: string;
  label: string | null;
  confidence: number;
  status: string;
  first_seen_at: string;
  last_seen_at: string;
  src_port: string | null;
  dst_port: string | null;
  // [KOS20260922] STP로 Blocking 처리된 링크를 구분해 보여주기 위해 노출한다.
  src_stp_state: string | null;
  dst_stp_state: string | null;
  // [KOS20260921] HIERARCHICAL: 두 장비의 depth가 달라 한쪽에선 Uplink, 반대쪽에선
  // Downlink인 연결. PEER: 같은 depth끼리의 연결(예: Core 이중화/Stack).
  link_role: "HIERARCHICAL" | "PEER";
}

export interface Topology {
  nodes: TopologyNode[];
  links: TopologyLink[];
}

export interface LinkPingResult {
  link_id: number;
  from_device_id: number;
  from_ip: string;
  to_device_id: number;
  to_ip: string;
  supported: boolean;
  success: boolean;
  protocol: "SSH" | "TELNET" | null;
  command: string | null;
  packet_loss_percent: number | null;
  rtt_min_ms: number | null;
  rtt_avg_ms: number | null;
  rtt_max_ms: number | null;
  output: string;
}

export interface DiscoveryRun {
  id: number;
  profile: string;
  cidr: string | null;
  status: "RUNNING" | "COMPLETED" | "FAILED";
  current_ip: string | null;
  current_ips: string | null; // JSON-encoded list, 동시 스캔 중인 IP 전체
  started_at: string;
  ended_at: string | null;
  scanned_count: number;
  alive_count: number;
  snmp_count: number;
  switch_count: number;
  camera_count: number;
  pc_count: number;
  unknown_count: number;
  error_count: number;
  concurrency: number;
  hostname_rules: string | null; // JSON-encoded list, e.g. '["DNS","NETBIOS"]'
}

export const HOSTNAME_RULE_LABELS: Record<string, string> = {
  DNS: "Reverse DNS(PTR)",
  NETBIOS: "NetBIOS(NBNS)",
  MDNS: "mDNS/Bonjour(.local)",
};

export interface ProfileProtocol {
  key: string;
  label: string;
  enabled: boolean;
}

export interface ProfileScope {
  profile: string;
  expand_neighbors: boolean;
  protocols: ProfileProtocol[];
}

export interface BulkDeleteResult {
  deleted: number[];
  skipped: number[];
}

export interface DismissResult {
  dismissed_count: number;
}

export interface ControlLog {
  id: number;
  device_id: number;
  interface_id: number | null;
  action: string;
  before_value: string | null;
  requested_value: string | null;
  after_value: string | null;
  result: "SUCCESS" | "FAILED" | "DENIED";
  error_message: string | null;
  performed_by: string;
  created_at: string;
}

export interface Alarm {
  id: string;
  severity: "CRITICAL" | "WARNING" | "INFO";
  category: "DEVICE" | "LINK" | "CONTROL" | "DISCOVERY";
  message: string;
  occurred_at: string;
  device_id: number | null;
  link_id: number | null;
  discovery_run_id: number | null;
  suppressed: boolean;
  suppressed_reason: string | null;
}

export interface CredentialProfile {
  id: number;
  name: string;
  has_snmp: boolean;
  has_ssh: boolean;
  has_telnet: boolean;
  has_cli: boolean;
  ssh_port: number;
  cli_protocol: "SSH" | "TELNET";
  created_at: string;
  updated_at: string;
}

export interface SystemInfo {
  schema_version: number;
  db_path: string;
  max_seed_hosts: number;
  max_discovery_depth: number;
  classification_threshold: number;
  classification_review_floor: number;
  core_score_threshold: number;
  floor_score_threshold: number;
  stale_after_seconds: number;
  offline_after_seconds: number;
  auto_discovery_enabled: boolean;
  auto_discovery_interval_seconds: number;
}

const BASE = "/api";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(body.detail ?? `요청 실패 (${response.status})`);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export const api = {
  listDevices: (params?: { device_type?: string; device_role?: string; status?: string; q?: string }) => {
    const qs = new URLSearchParams(Object.entries(params ?? {}).filter(([, v]) => v) as [string, string][]);
    const suffix = qs.toString() ? `?${qs.toString()}` : "";
    return request<Device[]>(`/devices${suffix}`);
  },
  getDevice: (id: number) => request<DeviceDetail>(`/devices/${id}`),
  diagnoseDevice: (id: number, kind: "ping" | "traceroute", protocol?: "TCP" | "UDP" | "ICMP") =>
    request<DeviceDiagnostic>(`/devices/${id}/diagnostics/${kind}${protocol ? `?protocol=${protocol}` : ""}`, { method: "POST" }),
  getL2PathEvidence: (id: number) => request<L2PathEvidence>(`/devices/${id}/diagnostics/l2-path`),
  reclassifyDevices: () => request<{ changed: number }>("/devices/reclassify", { method: "POST" }),
  getInterfaces: (id: number) => request<DeviceInterfaceRow[]>(`/devices/${id}/interfaces`),
  getNeighbors: (id: number) => request<NeighborRow[]>(`/devices/${id}/neighbors`),
  getMacTable: (id: number) => request<FdbRow[]>(`/devices/${id}/mac-table`),
  getArp: (id: number) => request<ArpRow[]>(`/devices/${id}/arp`),
  getRoutes: (id: number) => request<RouteRow[]>(`/devices/${id}/routes`),
  getPoe: (id: number) => request<PoeRow[]>(`/devices/${id}/poe`),
  getTopology: () => request<Topology>("/topology"),
  createTopologyLink: (payload: { src_device_id: number; dst_device_id: number; label: string }) =>
    request<TopologyLink>("/topology/links", { method: "POST", body: JSON.stringify(payload) }),
  deleteTopologyLink: (id: number) => request<void>(`/topology/links/${id}`, { method: "DELETE" }),
  pingTopologyLink: (
    id: number,
    credential?: {
      direction?: "FORWARD" | "REVERSE";
      protocol?: "SSH" | "TELNET";
      username?: string;
      password?: string;
      port?: number;
    },
  ) => request<LinkPingResult>(`/topology/links/${id}/ping`, {
    method: "POST",
    body: credential ? JSON.stringify(credential) : undefined,
  }),
  startDiscovery: (payload: {
    seed_targets?: string[];
    cidrs?: string[];
    profile: string;
    community?: string;
    concurrency?: number;
    hostname_rules?: string[];
    // [KOS20260922] true면 Run 시작 전 기존 network_link를 모두 지우고 이번
    // Discovery의 근거만으로 새로 쌓는다("기존 링크 정보를 모두 지우고 새로 하기").
    reset_links?: boolean;
    // [KOS20260922] true면 Run 시작 전 기존 장비를 모두 지운다(링크/인터페이스
    // /ARP/FDB 등도 함께 삭제됨). reset_links보다 훨씬 파괴적인 전체 초기화.
    reset_devices?: boolean;
  }) => request<DiscoveryRun>("/discovery/start", { method: "POST", body: JSON.stringify(payload) }),
  getDiscoveryRun: (runId: number) => request<DiscoveryRun>(`/discovery/${runId}`),
  listDiscoveryRuns: (limit = 20) => request<DiscoveryRun[]>(`/discovery?limit=${limit}`),
  getSeedSuggestions: () => request<{ subnets: string[] }>("/discovery/seed-suggestions"),
  getProfileScopes: () => request<ProfileScope[]>("/discovery/profiles"),
  deleteDiscoveryRun: (runId: number) => request<void>(`/discovery/${runId}`, { method: "DELETE" }),
  bulkDeleteDiscoveryRuns: (ids: number[]) =>
    request<BulkDeleteResult>("/discovery/bulk-delete", { method: "POST", body: JSON.stringify({ ids }) }),
  deleteAllDiscoveryRuns: () => request<BulkDeleteResult>("/discovery/delete-all", { method: "POST" }),
  listAlarms: (params?: { severity?: string; category?: string; include_suppressed?: boolean }) => {
    const qs = new URLSearchParams();
    if (params?.severity) qs.set("severity", params.severity);
    if (params?.category) qs.set("category", params.category);
    if (params?.include_suppressed === false) qs.set("include_suppressed", "false");
    const suffix = qs.toString() ? `?${qs.toString()}` : "";
    return request<Alarm[]>(`/alarms${suffix}`);
  },
  dismissAlarms: (alarms: { id: string; occurred_at: string }[]) =>
    request<DismissResult>("/alarms/dismiss", { method: "POST", body: JSON.stringify({ alarms }) }),
  dismissAllAlarms: (params?: { severity?: string; category?: string; include_suppressed?: boolean }) => {
    const qs = new URLSearchParams();
    if (params?.severity) qs.set("severity", params.severity);
    if (params?.category) qs.set("category", params.category);
    if (params?.include_suppressed === false) qs.set("include_suppressed", "false");
    const suffix = qs.toString() ? `?${qs.toString()}` : "";
    return request<DismissResult>(`/alarms/dismiss-all${suffix}`, { method: "POST" });
  },
  bulkDeleteControlLogs: (ids: number[]) =>
    request<BulkDeleteResult>("/control-logs/bulk-delete", { method: "POST", body: JSON.stringify({ ids }) }),
  deleteAllControlLogs: () => request<BulkDeleteResult>("/control-logs/delete-all", { method: "POST" }),
  bulkDeleteCredentialProfiles: (ids: number[]) =>
    request<BulkDeleteResult>("/credential-profiles/bulk-delete", { method: "POST", body: JSON.stringify({ ids }) }),
  deleteAllCredentialProfiles: () => request<BulkDeleteResult>("/credential-profiles/delete-all", { method: "POST" }),
  updateRole: (id: number, device_role: string, performed_by = "operator") =>
    request<Device>(`/devices/${id}/role`, {
      method: "PATCH",
      body: JSON.stringify({ device_role, performed_by }),
    }),
  enablePort: (deviceId: number, ifId: number, performed_by = "operator", force = false) =>
    request<ControlLog>(`/devices/${deviceId}/interfaces/${ifId}/enable`, {
      method: "POST",
      body: JSON.stringify({ performed_by, force }),
    }),
  disablePort: (deviceId: number, ifId: number, performed_by = "operator", force = false) =>
    request<ControlLog>(`/devices/${deviceId}/interfaces/${ifId}/disable`, {
      method: "POST",
      body: JSON.stringify({ performed_by, force }),
    }),
  enablePoe: (deviceId: number, ifId: number, performed_by = "operator", force = false) =>
    request<ControlLog>(`/devices/${deviceId}/interfaces/${ifId}/poe/enable`, {
      method: "POST",
      body: JSON.stringify({ performed_by, force }),
    }),
  disablePoe: (deviceId: number, ifId: number, performed_by = "operator", force = false) =>
    request<ControlLog>(`/devices/${deviceId}/interfaces/${ifId}/poe/disable`, {
      method: "POST",
      body: JSON.stringify({ performed_by, force }),
    }),
  listControlLogs: (params?: { device_id?: number; result?: string; limit?: number }) => {
    const qs = new URLSearchParams();
    if (params?.device_id != null) qs.set("device_id", String(params.device_id));
    if (params?.result) qs.set("result", params.result);
    if (params?.limit) qs.set("limit", String(params.limit));
    const suffix = qs.toString() ? `?${qs.toString()}` : "";
    return request<ControlLog[]>(`/control-logs${suffix}`);
  },
  listCredentialProfiles: () => request<CredentialProfile[]>("/credential-profiles"),
  createCredentialProfile: (snmp_community: string, name?: string) =>
    request<CredentialProfile>("/credential-profiles", { method: "POST", body: JSON.stringify({ snmp_community, name }) }),
  updateCredentialProfileSsh: (id: number, username: string, password: string, port: number, protocol: "SSH" | "TELNET") =>
    request<CredentialProfile>(`/credential-profiles/${id}/ssh`, {
      method: "PATCH",
      body: JSON.stringify({ username, password, port, protocol }),
    }),
  deleteCredentialProfile: (id: number) => request<void>(`/credential-profiles/${id}`, { method: "DELETE" }),
  getSystemInfo: () => request<SystemInfo>("/system-info"),
};
