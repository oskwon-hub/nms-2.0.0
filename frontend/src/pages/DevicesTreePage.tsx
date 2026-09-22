import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, Device } from "../api/client";
import StatusBadge from "../components/StatusBadge";
import { parseUtcDateTime } from "../lib/dateTime";

// 17.3절: Device Tree는 위치/역할/장비 수준까지 표시한다. Sensor/Interface 상세는
// Device Detail 화면(우측)에서 조회하도록 분리한다.
//
// [KOS20260922] "Devices의 목록이 Role별로 보이도록 수정해 줘" 요청 - 예전에는
// ROUTER를 role과 무관하게 별도 그룹으로 빼고, ACCESS_SWITCH를 L3/L2로,
// ENDPOINT/UNKNOWN을 device_type별(PC 종류)로 잘게 쪼갰다. Topology 화면의
// Role 구름(topologyRoleClouds.ts)과 device_role.py의 DEVICE_ROLES가 이미
// 정확히 8개 Role을 표준 분류 체계로 쓰고 있어, Devices 목록도 그 8개 Role을
// 그대로 최상위 그룹으로 쓰도록 단순화한다 - 세부 장비 종류(Router/L3-L2/PC
// 종류)는 그룹을 나누지 않아도 각 행의 device_type 텍스트로 여전히 바로
// 확인할 수 있으므로 정보 손실은 없다.
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
const GROUP_ORDER = DEVICE_ROLE_ORDER;

const GROUP_LABEL: Record<string, string> = {
  CORE_SWITCH: "Core Switch",
  DISTRIBUTION_SWITCH: "Distribution Switch",
  FLOOR_SWITCH: "Floor Switch",
  ACCESS_SWITCH: "Access Switch",
  EDGE_DEVICE: "Edge Device (AP/Hub)",
  SERVER: "Server",
  ENDPOINT: "Endpoint",
  UNKNOWN: "Unknown / Review Required",
};

function groupKeyFor(device: Device): string {
  return device.device_role || "UNKNOWN";
}

function ipSortKey(ip: string | null): number[] {
  const parts = (ip || "").split(".").map((p) => parseInt(p, 10));
  return [0, 1, 2, 3].map((i) => (Number.isFinite(parts[i]) ? parts[i] : 999));
}

function compareIp(a: string | null, b: string | null): number {
  const ka = ipSortKey(a);
  const kb = ipSortKey(b);
  for (let i = 0; i < 4; i++) {
    if (ka[i] !== kb[i]) return ka[i] - kb[i];
  }
  return 0;
}

export default function DevicesTreePage() {
  const [devices, setDevices] = useState<Device[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [collapsedRoles, setCollapsedRoles] = useState<Set<string>>(() => new Set());
  // [KOS20260921] 기본 정렬을 "모델명(hostname) 가나다순"으로 변경.
  const [sortBy, setSortBy] = useState<"name" | "ip">("name");
  const [reclassifying, setReclassifying] = useState(false);
  // [KOS20260921] "Discovery로 새로 발견된 장치를 Devices에서 표시해달라"는 요청 -
  // 가장 최근 Discovery Run의 시작 시각 이후에 first_seen_at이 찍힌 장비를 이번
  // 스캔에서 새로 발견된 것으로 보고 NEW 배지를 붙인다. 실행 중인 Run도 대상에
  // 포함되므로 스캔이 진행되는 동안에도 새로 잡히는 장비가 바로 표시된다.
  const [latestRunStartedAt, setLatestRunStartedAt] = useState<string | null>(null);
  const navigate = useNavigate();

  const reload = () => {
    setLoading(true);
    api
      .listDevices({ q: query || undefined, status: statusFilter || undefined })
      .then(setDevices)
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  };

  const handleReclassify = async () => {
    setReclassifying(true);
    setError(null);
    try {
      const result = await api.reclassifyDevices();
      reload();
      window.alert(`${result.changed}개 장비의 분류가 갱신되었습니다.`);
    } catch (err: any) {
      setError(err.message);
    } finally {
      setReclassifying(false);
    }
  };

  const toggleRole = (role: string) => {
    setCollapsedRoles((current) => {
      const next = new Set(current);
      if (next.has(role)) {
        next.delete(role);
      } else {
        next.add(role);
      }
      return next;
    });
  };

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api
      .listDevices({ q: query || undefined, status: statusFilter || undefined })
      .then((data) => {
        if (!cancelled) setDevices(data);
      })
      .catch((err) => !cancelled && setError(err.message))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [query, statusFilter]);

  useEffect(() => {
    api
      .listDiscoveryRuns(1)
      .then((runs) => setLatestRunStartedAt(runs[0]?.started_at ?? null))
      .catch(() => {});
  }, []);

  const newDeviceIds = useMemo(() => {
    if (!latestRunStartedAt) return new Set<number>();
    const threshold = parseUtcDateTime(latestRunStartedAt).getTime();
    return new Set(devices.filter((d) => parseUtcDateTime(d.first_seen_at).getTime() >= threshold).map((d) => d.id));
  }, [devices, latestRunStartedAt]);

  const grouped = useMemo(() => {
    const groups: Record<string, Device[]> = {};
    for (const device of devices) {
      const key = groupKeyFor(device);
      (groups[key] ??= []).push(device);
    }
    for (const key of Object.keys(groups)) {
      groups[key] = [...groups[key]].sort((a, b) => {
        if (sortBy === "name") return (a.hostname || "").localeCompare(b.hostname || "");
        return compareIp(a.management_ip, b.management_ip);
      });
    }
    return groups;
  }, [devices, sortBy]);

  return (
    <div>
      <div className="page-header">
        <h1>Devices</h1>
      </div>

      <div className="toolbar">
        <input
          type="search"
          placeholder="Hostname / IP 검색"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
          <option value="">전체 상태</option>
          <option value="ONLINE">ONLINE</option>
          <option value="STALE">STALE</option>
          <option value="OFFLINE">OFFLINE</option>
          <option value="UNKNOWN">UNKNOWN</option>
        </select>
        <select value={sortBy} onChange={(e) => setSortBy(e.target.value as "name" | "ip")}>
          <option value="name">이름순</option>
          <option value="ip">IP순</option>
        </select>
        <span className="muted">{devices.length}개 장비</span>
        {newDeviceIds.size > 0 && (
          <span className="badge badge-new" title="가장 최근 Discovery에서 새로 발견된 장비 수">
            NEW {newDeviceIds.size}
          </span>
        )}
        <button
          className="btn"
          disabled={reclassifying}
          onClick={handleReclassify}
          title="재탐색 없이 저장된 근거만으로 분류 규칙을 즉시 다시 적용합니다"
          style={{ marginLeft: "auto" }}
        >
          {reclassifying ? "재분류 중..." : "재분류"}
        </button>
      </div>

      {error && <div className="error-banner">{error}</div>}
      {loading && <div className="muted">불러오는 중...</div>}

      {!loading &&
        GROUP_ORDER.filter((key) => grouped[key]?.length).map((key) => (
          <div className="tree-group card" key={key}>
            <button
              type="button"
              className="tree-group-title tree-group-toggle"
              aria-expanded={!collapsedRoles.has(key)}
              aria-controls={`device-role-${key}`}
              onClick={() => toggleRole(key)}
            >
              <span>{GROUP_LABEL[key]} ({grouped[key].length})</span>
              <span className="tree-group-chevron" aria-hidden="true">
                {collapsedRoles.has(key) ? "▶" : "▼"}
              </span>
            </button>
            <div id={`device-role-${key}`} hidden={collapsedRoles.has(key)}>
              {grouped[key].map((device) => (
                <div className="device-row" key={device.id} onClick={() => navigate(`/devices/${device.id}`)}>
                  <StatusBadge status={device.status} />
                  <span className="hostname">{device.hostname || "(hostname 미확인)"}</span>
                  <span className="ip">{device.management_ip}</span>
                  <span className="type-role">
                    {device.device_type}
                    {device.physical_floor ? ` · ${device.physical_floor}F` : ""}
                    {device.role_source === "MANUAL" && <span className="badge badge-manual" style={{ marginLeft: 6 }}>MANUAL</span>}
                    {newDeviceIds.has(device.id) && (
                      <span className="badge badge-new" style={{ marginLeft: 6 }} title="가장 최근 Discovery에서 새로 발견됨">
                        NEW
                      </span>
                    )}
                  </span>
                </div>
              ))}
            </div>
          </div>
        ))}

      {!loading && devices.length === 0 && (
        <div className="card muted">발견된 장비가 없습니다. Discovery 메뉴에서 탐색을 시작하세요.</div>
      )}
    </div>
  );
}
