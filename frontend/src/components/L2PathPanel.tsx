import { useState } from "react";
import { api, L2PathEvidence, L2SwitchEvidence } from "../api/client";
import { formatUtcDateTime } from "../lib/dateTime";

const RELATION_LABELS: Record<L2SwitchEvidence["relation"], string> = {
  DIFFERENT_PORTS: "경유 후보 (서로 다른 포트)",
  SAME_PORT: "같은 포트 (경유 근거 없음)",
  ONE_SIDE: "한쪽 MAC만 학습",
  UNKNOWN_PORT: "포트 미확인",
};

export default function L2PathPanel({ deviceId, targetSysName, targetSysDescr, targetType }: { deviceId: number; targetSysName: string | null; targetSysDescr: string | null; targetType: string }) {
  const [evidence, setEvidence] = useState<L2PathEvidence | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const candidates = evidence?.switches.filter((item) => item.relation === "DIFFERENT_PORTS") ?? [];
  const samePortSwitches = evidence?.switches.filter((item) => item.relation === "SAME_PORT") ?? [];
  const targetName = targetSysName?.trim() || targetSysDescr?.trim() || targetType;

  const check = async () => {
    setBusy(true);
    setError(null);
    try {
      setEvidence(await api.getL2PathEvidence(deviceId));
    } catch (e) {
      setError(e instanceof Error ? e.message : "L2 경로 근거를 가져오지 못했습니다.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="l2-path-panel" aria-label="L2 경로 확인">
      <h3>L2 경로 확인</h3>
      <div className="muted">저장된 스위치 MAC 학습 포트로 경유 후보를 확인합니다. 정확한 경로에는 중간 스위치의 FDB와 스위치 간 LLDP/포트 연결 정보가 필요합니다.</div>
      <button className="btn" disabled={busy} onClick={check}>{busy ? "확인 중..." : evidence ? "저장된 근거 다시 읽기" : "L2 경로 확인"}</button>
      {error && <div className="error-banner" role="alert">{error}</div>}
      {evidence && (
        <div>
          <div className="l2-path-flow" aria-label="L2 경로 추정">
            <div className="l2-path-node"><strong>출발지 · NMS</strong><span>{evidence.source_ip ?? "IP 확인 불가"}</span><small>{evidence.source_mac ?? "MAC 확인 불가"}</small></div>
            <span aria-hidden="true">→</span>
            {candidates.length === 1 ? (
              <div className="l2-path-node l2-path-candidate">
                <strong>경유 후보 · 순서 미검증</strong>
                <span>{candidates[0].management_ip || `장비 #${candidates[0].switch_id}`}</span>
                <small>({candidates[0].sys_name?.trim() || candidates[0].sys_descr?.trim() || candidates[0].device_type})</small>
              </div>
            ) : (
              <div className="l2-path-node l2-path-unknown">
                <strong>L2 경로 미확인</strong>
                <span>{candidates.length > 1 ? `경유 후보 ${candidates.length}대 · 순서 미확인` : "확인된 경유 후보 없음"}</span>
              </div>
            )}
            <span aria-hidden="true">→</span>
            <div className="l2-path-node"><strong>목적지</strong><span>{evidence.target_ip}</span>{targetName && <small>({targetName})</small>}<small>{evidence.target_mac ?? "MAC 확인 불가"}</small></div>
          </div>
          {(!evidence.source_mac || !evidence.target_mac) && (
            <p className="muted">양쪽 MAC 주소가 모두 확인돼야 스위치 학습 포트를 대조할 수 있습니다.</p>
          )}
          {candidates.length === 0 && samePortSwitches.length > 0 && (
            <p className="muted">
              {samePortSwitches.map((item) => item.hostname || item.management_ip || `#${item.switch_id}`).join(", ")}에서는
              두 MAC이 같은 포트에서 보여 경유 스위치로 확인할 수 없습니다. 그 포트 뒤쪽 스위치들의 FDB와 연결 정보를 수집해야 합니다.
            </p>
          )}
          {evidence.switches.length > 0 && (
            <div className="diagnostic-hops">
              <table>
                <thead><tr><th>스위치</th><th>VLAN</th><th>NMS 측 학습 포트</th><th>목적지 측 학습 포트</th><th>판단</th><th>마지막 수집</th></tr></thead>
                <tbody>
                  {evidence.switches.map((item) => (
                    <tr key={`${item.switch_id}-${item.vlan ?? "none"}`}>
                      <td>{item.hostname || item.management_ip || `#${item.switch_id}`}</td>
                      <td>{item.vlan ?? "미확인"}</td>
                      <td>{item.source_ports.join(", ") || "-"}</td>
                      <td>{item.target_ports.join(", ") || "-"}</td>
                      <td>{RELATION_LABELS[item.relation]}</td>
                      <td>NMS {formatUtcDateTime(item.source_seen_at)} / 목적지 {formatUtcDateTime(item.target_seen_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <p className="muted">저장된 FDB 근거입니다. Discovery를 다시 실행해도 스위치가 FDB/LLDP를 SNMP로 제공하지 않으면 경로는 계속 미확인으로 남습니다.</p>
        </div>
      )}
    </section>
  );
}
