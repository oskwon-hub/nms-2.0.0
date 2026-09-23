import { useEffect, useState } from "react";
import {
  api,
  ArpRow,
  ConfigChangeLog,
  DeviceDetail,
  DeviceInterfaceRow,
  FdbRow,
  NeighborRow,
  PoeRow,
  RouteRow,
} from "../api/client";
import StatusBadge from "./StatusBadge";
import DeviceOverviewPanel from "./DeviceOverviewPanel";
import DeviceDiagnostics from "./DeviceDiagnostics";
import { formatUtcDateTime } from "../lib/dateTime";

type Tab = "overview" | "interfaces" | "neighbors" | "fdb" | "arp" | "routes" | "poe" | "history";

// ConfigurationPage.tsx와 같은 매핑 - 여기서도 필드 이름을 그대로 노출하지 않고
// 한글 라벨로 바꾼다.
const FIELD_LABELS: Record<string, string> = {
  admin_status: "포트 Admin 상태",
  vlan: "VLAN (PVID)",
  is_stp_root: "STP Root 여부",
  device_role: "Role",
  management_ip: "관리 IP",
  os_version: "OS 버전",
  firmware_version: "펌웨어 버전",
};

function fieldLabel(fieldName: string): string {
  return FIELD_LABELS[fieldName] ?? fieldName;
}

// backend/app/control/config_restore.py의 RESTORABLE_FIELDS와 맞춘다.
const RESTORABLE_FIELDS = new Set(["admin_status", "device_role"]);

function formatBytes(value: number | null): string {
  if (value == null) return "-";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let n = value;
  let unitIndex = 0;
  while (n >= 1024 && unitIndex < units.length - 1) {
    n /= 1024;
    unitIndex += 1;
  }
  return `${n.toFixed(unitIndex === 0 ? 0 : 1)}${units[unitIndex]}`;
}

// [KOS20260922] dot1dStpPortState를 상태별로 다른 배지 색으로 보여준다 -
// BLOCKING은 물리적으로는 연결돼 있지만 STP가 논리적으로 막아둔 포트라는 뜻이라
// FORWARDING과 시각적으로 뚜렷하게 구분돼야 한다.
function renderStpState(state: string | null): React.ReactNode {
  if (!state) return <span className="muted">-</span>;
  const cls =
    state === "FORWARDING" ? "badge-online" : state === "BLOCKING" ? "badge-offline" : state === "DISABLED" ? "badge-unknown" : "badge-stale";
  return <span className={`badge ${cls}`}>{state}</span>;
}

interface DeviceDetailContentProps {
  deviceId: number;
  // [KOS20260921] DeviceDetailPage(라우트 전체 페이지)와 Topology의 상세 보기
  // 팝업(Modal)이 이 컴포넌트를 그대로 공유한다 - 헤더 오른쪽에 "← 뒤로" 버튼을
  // 넣을지(페이지 라우트) 닫기 버튼을 넣을지(팝업)는 호출하는 쪽이 결정한다.
  headerExtra?: React.ReactNode;
}

export default function DeviceDetailContent({ deviceId, headerExtra }: DeviceDetailContentProps) {
  const [device, setDevice] = useState<DeviceDetail | null>(null);
  const [interfaces, setInterfaces] = useState<DeviceInterfaceRow[]>([]);
  const [neighbors, setNeighbors] = useState<NeighborRow[]>([]);
  const [fdb, setFdb] = useState<FdbRow[]>([]);
  const [arp, setArp] = useState<ArpRow[]>([]);
  const [routes, setRoutes] = useState<RouteRow[]>([]);
  const [poe, setPoe] = useState<PoeRow[]>([]);
  const [configChanges, setConfigChanges] = useState<ConfigChangeLog[]>([]);
  const [tab, setTab] = useState<Tab>("overview");
  const [error, setError] = useState<string | null>(null);
  const [busyIfId, setBusyIfId] = useState<number | null>(null);

  const reload = () => {
    if (!deviceId) return;
    api.getDevice(deviceId).then(setDevice).catch((e) => setError(e.message));
    api.getInterfaces(deviceId).then(setInterfaces).catch(() => {});
    api.getNeighbors(deviceId).then(setNeighbors).catch(() => {});
    api.getMacTable(deviceId).then(setFdb).catch(() => {});
    api.getArp(deviceId).then(setArp).catch(() => {});
    api.getRoutes(deviceId).then(setRoutes).catch(() => {});
    api.getPoe(deviceId).then(setPoe).catch(() => {});
    api.listConfigChanges({ device_id: deviceId, limit: 200 }).then(setConfigChanges).catch(() => {});
  };

  useEffect(reload, [deviceId]);

  const handleRoleChange = async (role: string) => {
    if (!device) return;
    try {
      const updated = await api.updateRole(device.id, role);
      setDevice({ ...device, ...updated });
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handlePortAction = async (iface: DeviceInterfaceRow, action: "enable" | "disable") => {
    if (!device) return;
    if (action === "disable" && iface.is_protected) {
      const ok = window.confirm(
        `${iface.name} 포트는 보호 포트(${iface.protected_reason})입니다. 강제로 Disable 하시겠습니까?`,
      );
      if (!ok) return;
    }
    setBusyIfId(iface.id);
    setError(null);
    try {
      const fn = action === "enable" ? api.enablePort : api.disablePort;
      await fn(device.id, iface.id, "operator", iface.is_protected);
      reload();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusyIfId(null);
    }
  };

  const handlePoeAction = async (iface: DeviceInterfaceRow, action: "enable" | "disable") => {
    if (!device) return;
    if (action === "disable" && iface.is_protected) {
      const ok = window.confirm(`${iface.name}의 PoE Disable은 서비스 중단을 유발할 수 있습니다. 계속할까요?`);
      if (!ok) return;
    }
    setBusyIfId(iface.id);
    setError(null);
    try {
      const fn = action === "enable" ? api.enablePoe : api.disablePoe;
      await fn(device.id, iface.id, "operator", iface.is_protected);
      reload();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusyIfId(null);
    }
  };

  const [vlanDrafts, setVlanDrafts] = useState<Record<number, string>>({});
  const [descDrafts, setDescDrafts] = useState<Record<number, string>>({});

  const handleVlanSave = async (iface: DeviceInterfaceRow) => {
    if (!device) return;
    const raw = vlanDrafts[iface.id] ?? (iface.vlan != null ? String(iface.vlan) : "");
    const vlan = parseInt(raw, 10);
    if (Number.isNaN(vlan)) {
      setError("VLAN은 숫자여야 합니다.");
      return;
    }
    if (
      iface.is_protected &&
      !window.confirm(`${iface.name} 포트는 보호 포트(${iface.protected_reason})입니다. 강제로 VLAN을 변경하시겠습니까?`)
    ) {
      return;
    }
    setBusyIfId(iface.id);
    setError(null);
    try {
      await api.setVlan(device.id, iface.id, vlan, "operator", iface.is_protected);
      reload();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusyIfId(null);
    }
  };

  const handleDescriptionSave = async (iface: DeviceInterfaceRow) => {
    if (!device) return;
    const description = descDrafts[iface.id] ?? "";
    if (
      iface.is_protected &&
      !window.confirm(`${iface.name} 포트는 보호 포트(${iface.protected_reason})입니다. 강제로 설명을 변경하시겠습니까?`)
    ) {
      return;
    }
    setBusyIfId(iface.id);
    setError(null);
    try {
      await api.setDescription(device.id, iface.id, description, "operator", iface.is_protected);
      setDescDrafts((prev) => ({ ...prev, [iface.id]: "" }));
      reload();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusyIfId(null);
    }
  };

  const [restoringChangeId, setRestoringChangeId] = useState<number | null>(null);
  const handleRestoreConfigChange = async (change: ConfigChangeLog) => {
    if (
      !window.confirm(`${fieldLabel(change.field_name)}을(를) "${change.old_value ?? "(없음)"}"(으)로 복구할까요?`)
    ) {
      return;
    }
    setRestoringChangeId(change.id);
    setError(null);
    try {
      const result = await api.restoreConfigChange(change.id);
      if (result.result !== "SUCCESS") {
        setError(result.error_message || `복구 실패 (${result.result})`);
      }
      reload();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setRestoringChangeId(null);
    }
  };

  if (!device) {
    return (
      <div>
        {headerExtra}
        {error ? <div className="error-banner">{error}</div> : <div className="muted">불러오는 중...</div>}
      </div>
    );
  }

  return (
    <div>
      <div className="page-header">
        <h1>
          {device.hostname || device.management_ip} <StatusBadge status={device.status} />
        </h1>
        {headerExtra}
      </div>

      {error && <div className="error-banner">{error}</div>}

      <div className="tabs">
        {(["overview", "interfaces", "neighbors", "fdb", "arp", "routes", "poe", "history"] as Tab[]).map((t) => (
          <button key={t} className={tab === t ? "active" : ""} onClick={() => setTab(t)}>
            {t.toUpperCase()}
          </button>
        ))}
      </div>

      {tab === "overview" && (
        <div className="card">
          <DeviceOverviewPanel device={device} onRoleChange={handleRoleChange} />
          <DeviceDiagnostics
            key={device.id}
            deviceId={device.id}
            managementIp={device.management_ip}
            targetHostname={device.hostname}
            targetSysName={device.model}
            targetSysDescr={device.sys_descr}
            targetType={device.device_type}
            targetRole={device.device_role}
          />
        </div>
      )}

      {tab === "interfaces" && (
        <div className="card">
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>MAC</th>
                <th>Admin</th>
                <th>Oper</th>
                <th>Speed</th>
                <th title="마지막 수집 시점까지의 누적 수신 바이트">RX</th>
                <th title="마지막 수집 시점까지의 누적 송신 바이트">TX</th>
                <th title="BRIDGE-MIB dot1dStpPortState">STP</th>
                <th>링크 방향</th>
                <th>Protected</th>
                <th>VLAN</th>
                <th title="ifAlias는 상시 조회하지 않아 현재 값을 표시하지 않습니다. 입력하면 새 값으로 설정만 합니다.">설명 설정</th>
                <th>제어</th>
              </tr>
            </thead>
            <tbody>
              {interfaces.map((iface) => (
                <tr key={iface.id}>
                  <td>
                    {iface.name}{" "}
                    <span className="muted" style={{ fontSize: 11 }} title="ifIndex - 같은 이름의 인터페이스를 구분할 때 사용">
                      #{iface.if_index}
                    </span>
                  </td>
                  <td className="muted">{iface.mac}</td>
                  <td>{iface.admin_status}</td>
                  <td>{iface.oper_status}</td>
                  <td>{iface.speed_mbps ? `${iface.speed_mbps}Mbps` : "-"}</td>
                  <td className="muted">{formatBytes(iface.in_octets)}</td>
                  <td className="muted">{formatBytes(iface.out_octets)}</td>
                  <td>{renderStpState(iface.stp_state)}</td>
                  <td>
                    {iface.is_uplink && (
                      <span className="badge badge-online" title="상위(Core 방향) 장비로 연결">
                        Uplink
                      </span>
                    )}{" "}
                    {iface.is_downlink && (
                      <span className="badge badge-stale" title="하위(단말 방향) 장비로 연결">
                        Downlink
                      </span>
                    )}{" "}
                    {iface.is_trunk && <span className="badge badge-manual">Trunk</span>}
                    {!iface.is_uplink && !iface.is_downlink && !iface.is_trunk && <span className="muted">-</span>}
                  </td>
                  <td>{iface.is_protected ? <span className="badge badge-manual">{iface.protected_reason}</span> : "-"}</td>
                  <td>
                    <input
                      type="number"
                      value={vlanDrafts[iface.id] ?? (iface.vlan != null ? String(iface.vlan) : "")}
                      onChange={(e) => setVlanDrafts((prev) => ({ ...prev, [iface.id]: e.target.value }))}
                      style={{ width: 64 }}
                      aria-label={`${iface.name} VLAN`}
                    />{" "}
                    <button className="btn" disabled={busyIfId === iface.id} onClick={() => handleVlanSave(iface)}>
                      저장
                    </button>
                  </td>
                  <td>
                    <input
                      type="text"
                      placeholder="새 설명"
                      value={descDrafts[iface.id] ?? ""}
                      onChange={(e) => setDescDrafts((prev) => ({ ...prev, [iface.id]: e.target.value }))}
                      style={{ width: 120 }}
                      aria-label={`${iface.name} 설명`}
                    />{" "}
                    <button className="btn" disabled={busyIfId === iface.id} onClick={() => handleDescriptionSave(iface)}>
                      설정
                    </button>
                  </td>
                  <td>
                    <button
                      className="btn"
                      disabled={busyIfId === iface.id || iface.admin_status === "UP"}
                      onClick={() => handlePortAction(iface, "enable")}
                    >
                      Enable
                    </button>{" "}
                    <button
                      className="btn btn-danger"
                      disabled={busyIfId === iface.id || iface.admin_status === "DOWN"}
                      onClick={() => handlePortAction(iface, "disable")}
                    >
                      Disable
                    </button>{" "}
                    {iface.poe_capable && (
                      <>
                        <button className="btn" disabled={busyIfId === iface.id} onClick={() => handlePoeAction(iface, "enable")}>
                          PoE On
                        </button>{" "}
                        <button className="btn btn-danger" disabled={busyIfId === iface.id} onClick={() => handlePoeAction(iface, "disable")}>
                          PoE Off
                        </button>
                      </>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {tab === "neighbors" && (
        <div className="card">
          <table>
            <thead>
              <tr>
                <th>Protocol</th>
                <th>Remote Chassis</th>
                <th>Remote Port</th>
                <th>Remote SysName</th>
                <th>Remote Mgmt IP</th>
              </tr>
            </thead>
            <tbody>
              {neighbors.map((n) => (
                <tr key={n.id}>
                  <td>{n.protocol}</td>
                  <td className="muted">{n.remote_chassis_id}</td>
                  <td>{n.remote_port_id}</td>
                  <td>{n.remote_sys_name}</td>
                  <td>{n.remote_mgmt_ip}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {tab === "fdb" && (
        <div className="card">
          <table>
            <thead>
              <tr>
                <th>MAC</th>
                <th>VLAN</th>
                <th>Type</th>
              </tr>
            </thead>
            <tbody>
              {fdb.map((f) => (
                <tr key={f.id}>
                  <td className="muted">{f.mac}</td>
                  <td>{f.vlan ?? "-"}</td>
                  <td>{f.entry_type}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {tab === "arp" && (
        <div className="card">
          <table>
            <thead>
              <tr>
                <th>IP</th>
                <th>MAC</th>
                <th>State</th>
              </tr>
            </thead>
            <tbody>
              {arp.map((a) => (
                <tr key={a.id}>
                  <td>{a.ip}</td>
                  <td className="muted">{a.mac}</td>
                  <td>{a.state}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {tab === "routes" && (
        <div className="card">
          <table>
            <thead>
              <tr>
                <th>Destination</th>
                <th>Next Hop</th>
                <th>Type</th>
              </tr>
            </thead>
            <tbody>
              {routes.map((r) => (
                <tr key={r.id}>
                  <td>
                    {r.destination}/{r.prefix_len}
                  </td>
                  <td>{r.next_hop}</td>
                  <td>{r.route_type}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {tab === "poe" && (
        <div className="card">
          {poe.length === 0 ? (
            <div className="muted">수집된 PoE 정보가 없습니다. 이 장비를 STANDARD 또는 DETAILED 프로필로 다시 탐색해 주세요.</div>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Interface</th>
                  <th>Enabled</th>
                  <th>Status</th>
                  <th>Power</th>
                  <th>Class</th>
                </tr>
              </thead>
              <tbody>
                {poe.map((p) => (
                  <tr key={p.id}>
                    <td>{interfaces.find((i) => i.id === p.interface_id)?.name ?? p.interface_id}</td>
                    <td>{String(p.enabled)}</td>
                    <td>{p.status}</td>
                    <td>{p.power_mw != null ? `${(p.power_mw / 1000).toFixed(1)} W` : "-"}</td>
                    <td>{p.poe_class ?? "-"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {tab === "history" && (
        <div className="card">
          {configChanges.length === 0 ? (
            <div className="muted">기록된 구성 변경이 없습니다.</div>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>시각</th>
                  <th>필드</th>
                  <th>이전 → 새 값</th>
                  <th>주체</th>
                  <th>수행자</th>
                  <th>복구</th>
                </tr>
              </thead>
              <tbody>
                {configChanges.map((c) => (
                  <tr key={c.id}>
                    <td className="muted">{formatUtcDateTime(c.detected_at)}</td>
                    <td>{fieldLabel(c.field_name)}</td>
                    <td className="muted">
                      {c.old_value ?? "(없음)"} → {c.new_value ?? "(없음)"}
                    </td>
                    <td>
                      <span className={`badge ${c.source === "MANUAL" ? "badge-stale" : "badge-online"}`}>
                        {c.source === "MANUAL" ? "수동 변경" : "재탐색 감지"}
                      </span>
                    </td>
                    <td className="muted">{c.performed_by ?? "-"}</td>
                    <td>
                      {RESTORABLE_FIELDS.has(c.field_name) ? (
                        <button
                          className="btn"
                          disabled={restoringChangeId === c.id}
                          onClick={() => handleRestoreConfigChange(c)}
                        >
                          {restoringChangeId === c.id ? "복구 중..." : "복구"}
                        </button>
                      ) : (
                        <span className="muted">지원 안 함</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </div>
  );
}
