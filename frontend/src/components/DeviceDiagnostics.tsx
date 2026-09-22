import { useState } from "react";
import { api, DeviceDiagnostic } from "../api/client";
import L2PathPanel from "./L2PathPanel";

type DiagnosticKind = "ping" | "traceroute";
type TraceProtocol = "TCP" | "UDP" | "ICMP";

export default function DeviceDiagnostics({
  deviceId,
  managementIp,
  targetHostname,
  targetSysName,
  targetSysDescr,
  targetType,
  targetRole,
}: {
  deviceId: number;
  managementIp: string | null;
  targetHostname: string | null;
  targetSysName: string | null;
  targetSysDescr: string | null;
  targetType: string;
  targetRole: string;
}) {
  const [running, setRunning] = useState<DiagnosticKind | null>(null);
  const [result, setResult] = useState<DeviceDiagnostic | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [traceProtocol, setTraceProtocol] = useState<TraceProtocol>("UDP");

  const run = async (kind: DiagnosticKind) => {
    setRunning(kind);
    setResult(null);
    setError(null);
    try {
      setResult(await api.diagnoseDevice(deviceId, kind, kind === "traceroute" ? traceProtocol : "ICMP"));
    } catch (e) {
      setError(e instanceof Error ? e.message : "진단 요청에 실패했습니다.");
    } finally {
      setRunning(null);
    }
  };

  const viaHops = result?.hops.filter((hop) => hop.ip !== result.target && hop.ip !== result.source_ip) ?? [];
  const destinationHop = result?.hops.find((hop) => hop.ip === result.target);

  return (
    <section className="device-diagnostics" aria-label="네트워크 진단">
      <h2>네트워크 진단</h2>
      <p className="muted">NMS 서버에서 관리 IP {managementIp ?? "없음"}(으)로 실행합니다.</p>
      <div className="toolbar">
        <button className="btn" disabled={!managementIp || running !== null} onClick={() => run("ping")}>
          {running === "ping" ? "Ping 실행 중..." : "Ping test"}
        </button>
        <label htmlFor={`trace-protocol-${deviceId}`}>Trace route 방식</label>
        <select id={`trace-protocol-${deviceId}`} value={traceProtocol} disabled={running !== null} onChange={(e) => setTraceProtocol(e.target.value as TraceProtocol)}>
          <option value="TCP">TCP</option>
          <option value="UDP">UDP</option>
          <option value="ICMP">ICMP</option>
        </select>
        <button className="btn" disabled={!managementIp || running !== null} onClick={() => run("traceroute")}>
          {running === "traceroute" ? "Trace route 실행 중..." : "Trace route"}
        </button>
      </div>
      {!managementIp && <div className="muted">관리 IP가 없어 진단할 수 없습니다.</div>}
      {error && <div className="error-banner" role="alert">{error}</div>}
      {result && (
        <div role="status">
          <strong>{result.command} ({result.protocol}) → {result.target}: {result.success ? "완료" : "실패"}</strong>
          {result.command !== "ping" && (
            <div className="diagnostic-hops">
              <h3>경로</h3>
              <table>
                <thead>
                  <tr><th>구분</th><th>홉</th><th>IP</th><th>장비</th><th>역할</th></tr>
                </thead>
                <tbody>
                  <tr>
                    <td>출발지</td>
                    <td>-</td>
                    <td>{result.source_ip ?? "확인 불가"}</td>
                    <td>NMS 서버</td>
                    <td>-</td>
                  </tr>
                  {viaHops.map((hop, index) => (
                    <tr key={`${hop.hop}-${index}`}>
                      <td>경유지</td>
                      <td>{hop.hop}</td>
                      <td>{hop.ip ?? "응답 없음"}</td>
                      <td>{hop.ip ? (hop.device_id ? (hop.hostname || `장비 #${hop.device_id}`) : "인벤토리 미등록") : "-"}</td>
                      <td>{hop.device_role ?? "-"}</td>
                    </tr>
                  ))}
                  <tr>
                    <td>{destinationHop ? "목적지" : "목적지 (응답 미확인)"}</td>
                    <td>{destinationHop?.hop ?? "-"}</td>
                    <td>{result.target}</td>
                    <td>{targetHostname || `장비 #${deviceId}`}</td>
                    <td>{targetRole}</td>
                  </tr>
                </tbody>
              </table>
              <p className="muted">
                {viaHops.length === 0 ? "관측된 경유 L3 홉이 없습니다. " : ""}
                경유지는 응답한 L3 홉만 표시하며, L2 스위치는 나타나지 않을 수 있습니다.
              </p>
            </div>
          )}
          <pre className="diagnostic-output">{result.output}</pre>
        </div>
      )}
      {managementIp && <L2PathPanel deviceId={deviceId} targetSysName={targetSysName} targetSysDescr={targetSysDescr} targetType={targetType} />}
    </section>
  );
}
