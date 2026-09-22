import { DeviceDetail } from "../api/client";
import { formatUtcDateTime } from "../lib/dateTime";

// Device Detail 페이지의 Overview 탭과 Topology 노드 선택 패널이 동일한 정보를
// 보여줘야 한다는 요구사항에 따라 공통 컴포넌트로 분리했다.
export const DEVICE_ROLES = [
  "CORE_SWITCH",
  "DISTRIBUTION_SWITCH",
  "FLOOR_SWITCH",
  "ACCESS_SWITCH",
  "EDGE_DEVICE",
  "SERVER",
  "ENDPOINT",
  "UNKNOWN",
];

export default function DeviceOverviewPanel({
  device,
  onRoleChange,
}: {
  device: DeviceDetail;
  onRoleChange: (role: string) => void;
}) {
  return (
    <div>
      <table>
        <tbody>
          <tr>
            <th>Management IP</th>
            <td>{device.management_ip}</td>
          </tr>
          <tr>
            <th>MAC</th>
            <td>{device.primary_mac}</td>
          </tr>
          <tr>
            <th>Vendor / Model</th>
            <td>
              {device.vendor || "-"} / {device.model || "-"}
            </td>
          </tr>
          {device.sys_descr && (
            <tr>
              <th>System Description</th>
              <td className="muted" style={{ whiteSpace: "pre-wrap" }}>
                {device.sys_descr}
              </td>
            </tr>
          )}
          <tr>
            <th>Device Type</th>
            <td>
              {device.device_type} (score {device.classification_score}, {device.classification_method})
            </td>
          </tr>
          {device.is_stp_root && (
            <tr>
              <th>STP</th>
              <td>
                <span className="badge badge-manual" title="BRIDGE-MIB dot1dStpRootPort == 0">
                  ★ ROOT BRIDGE
                </span>
              </td>
            </tr>
          )}
          <tr>
            <th>Device Role</th>
            <td>
              <select value={device.device_role} onChange={(e) => onRoleChange(e.target.value)}>
                {DEVICE_ROLES.map((r) => (
                  <option key={r} value={r}>
                    {r}
                  </option>
                ))}
              </select>{" "}
              <span className="muted">
                (score {device.role_score}, source {device.role_source})
              </span>
            </td>
          </tr>
          <tr>
            <th>Floor</th>
            <td>
              {device.physical_floor ? `${device.physical_floor}F` : "-"} {device.floor_source && `(${device.floor_source})`}
            </td>
          </tr>
          <tr>
            <th>Capabilities</th>
            <td>
              L2:{String(device.layer2_capable)} L3:{String(device.layer3_capable)} PoE:{String(device.poe_capable)}
            </td>
          </tr>
          <tr>
            <th>Discovery Depth</th>
            <td>{device.discovery_depth}</td>
          </tr>
          <tr>
            <th>Last Seen</th>
            <td>{formatUtcDateTime(device.last_seen_at)}</td>
          </tr>
        </tbody>
      </table>
      {device.classification_detail && (
        <>
          <div className="tree-group-title" style={{ marginTop: 12 }}>
            분류 근거 (Evidence)
          </div>
          <EvidenceList json={device.classification_detail} />
        </>
      )}
    </div>
  );
}

export function EvidenceList({ json }: { json: string }) {
  try {
    const items: { evidence: string; score: number }[] = JSON.parse(json);
    return (
      <ul className="evidence-list">
        {items.map((item, idx) => (
          <li key={idx}>
            {item.evidence}: +{item.score}
          </li>
        ))}
      </ul>
    );
  } catch {
    return null;
  }
}
