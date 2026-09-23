import { useEffect, useState } from "react";
import { api, DeviceDiagnostic, LinkPingResult, Topology, TopologyLink } from "../api/client";

type Direction = "FORWARD" | "REVERSE";

export default function LinkPingDiagnostics({ links, topology }: { links: TopologyLink[]; topology: Topology }) {
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<LinkPingResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [protocol, setProtocol] = useState<"SSH" | "TELNET">("SSH");
  const [selection, setSelection] = useState(() => `${links[0]?.id ?? 0}:FORWARD`);
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [port, setPort] = useState(22);
  const endpointIds = Array.from(new Set(links.flatMap((item) => [item.src_device_id, item.dst_device_id])));
  const [nmsTargetId, setNmsTargetId] = useState(endpointIds[0] ?? 0);
  const [nmsTargetIp, setNmsTargetIp] = useState(
    topology.nodes.find((node) => node.id === (endpointIds[0] ?? 0))?.management_ip ?? "",
  );
  const [nmsRunning, setNmsRunning] = useState(false);
  const [nmsResult, setNmsResult] = useState<DeviceDiagnostic | null>(null);
  const [nmsError, setNmsError] = useState<string | null>(null);

  const linksKey = links.map((item) => item.id).join(",");
  const [selectedLinkIdText, selectedDirectionText] = selection.split(":");
  const selectedLinkId = Number(selectedLinkIdText);
  const direction: Direction = selectedDirectionText === "REVERSE" ? "REVERSE" : "FORWARD";
  const link = links.find((item) => item.id === selectedLinkId) ?? links[0];

  const nodeLabel = (id: number): string => {
    const node = topology.nodes.find((item) => item.id === id);
    return node?.hostname || node?.management_ip || `#${id}`;
  };

  const endpointRole = (id: number): string => {
    const from = links.some((item) => item.src_device_id === id);
    const to = links.some((item) => item.dst_device_id === id);
    return from && to ? "From/To" : from ? "From" : "To";
  };

  useEffect(() => {
    setResult(null);
    setError(null);
    if (!links.some((item) => item.id === selectedLinkId)) {
      setSelection(`${links[0]?.id ?? 0}:FORWARD`);
    }
    if (!endpointIds.includes(nmsTargetId)) setNmsTargetId(endpointIds[0] ?? 0);
    setNmsResult(null);
    setNmsError(null);
  }, [linksKey, selectedLinkId]);

  useEffect(() => {
    const target = topology.nodes.find((node) => node.id === nmsTargetId);
    setNmsTargetIp(target?.management_ip ?? "");
    setNmsResult(null);
    setNmsError(null);
  }, [nmsTargetId, topology.nodes]);

  useEffect(() => {
    const sourceId = direction === "REVERSE" ? link.dst_device_id : link.src_device_id;
    const source = topology.nodes.find((node) => node.id === sourceId);
    const identity = `${source?.hostname ?? ""} ${source?.sys_name ?? ""} ${source?.device_type ?? ""}`.trim().toUpperCase();
    const nextProtocol = identity.startsWith("NSH") ? "TELNET" : "SSH";
    setProtocol(nextProtocol);
    setPort(nextProtocol === "TELNET" ? 23 : 22);
  }, [direction, link?.dst_device_id, link?.src_device_id, topology.nodes]);

  if (!link) return null;

  const sourceId = direction === "REVERSE" ? link.dst_device_id : link.src_device_id;
  const source = topology.nodes.find((node) => node.id === sourceId);
  const sourceIdentity = `${source?.hostname ?? ""} ${source?.sys_name ?? ""} ${source?.device_type ?? ""}`.trim().toUpperCase();
  const defaultName = sourceIdentity.startsWith("NSH")
    ? "NSH 기본 Credential"
    : sourceIdentity.startsWith("NHM")
      ? "NHM 기본 Credential"
      : "장비 Credential Profile";

  const nmsTarget = topology.nodes.find((node) => node.id === nmsTargetId);

  const runNmsPing = async () => {
    const targetIp = nmsTargetIp.trim();
    if (!targetIp) {
      setNmsError("Ping 대상 IP를 입력해 주세요.");
      return;
    }
    setNmsRunning(true);
    setNmsResult(null);
    setNmsError(null);
    try {
      setNmsResult(await api.pingFromNms(targetIp));
    } catch (e) {
      setNmsError(e instanceof Error ? e.message : "NMS 서버 Ping 요청에 실패했습니다.");
    } finally {
      setNmsRunning(false);
    }
  };

  const run = async () => {
    if (password && !username.trim()) {
      setError("CLI ID를 입력해 주세요.");
      return;
    }
    setRunning(true);
    setResult(null);
    setError(null);
    try {
      setResult(await api.pingTopologyLink(link.id, password ? {
        direction,
        protocol,
        username: username.trim(),
        password,
        port,
      } : {
        direction,
        protocol,
        username: username.trim() || undefined,
        port,
      }));
    } catch (e) {
      setError(e instanceof Error ? e.message : "링크 Ping 요청에 실패했습니다.");
    } finally {
      setRunning(false);
    }
  };

  return (
    <section className="device-diagnostics" aria-label="링크 네트워크 진단">
      <h2>NMS 서버 → 노드 Ping</h2>
      <p className="muted">NMS 서버에서 선택한 링크 장비의 관리 IP로 Ping을 보냅니다.</p>
      <select
        aria-label="NMS Ping 대상 노드"
        value={nmsTargetId}
        onChange={(e) => {
          setNmsTargetId(Number(e.target.value));
          setNmsResult(null);
          setNmsError(null);
        }}
        style={{ width: "100%", marginBottom: 8 }}
      >
        {endpointIds.map((id) => {
          const node = topology.nodes.find((item) => item.id === id);
          return (
            <option key={id} value={id}>
              {endpointRole(id)} · {nodeLabel(id)} · {node?.management_ip ?? "관리 IP 없음"}
            </option>
          );
        })}
      </select>
      <input
        type="text"
        inputMode="text"
        aria-label="NMS Ping 대상 IP"
        placeholder="Ping 대상 IPv4 또는 IPv6"
        value={nmsTargetIp}
        onChange={(e) => {
          setNmsTargetIp(e.target.value);
          setNmsResult(null);
          setNmsError(null);
        }}
        onKeyDown={(e) => { if (e.key === "Enter") runNmsPing(); }}
        style={{ width: "100%", marginBottom: 8 }}
      />
      <button
        className="btn"
        style={{ width: "100%" }}
        disabled={nmsRunning || !nmsTargetIp.trim()}
        onClick={runNmsPing}
      >
        {nmsRunning ? "Ping 실행 중..." : "NMS 서버에서 Ping"}
      </button>
      {nmsTarget && !nmsTarget.management_ip && !nmsTargetIp && <div className="muted" style={{ marginTop: 8 }}>선택한 장비에 관리 IP가 없습니다. 대상 IP를 직접 입력해 주세요.</div>}
      {nmsError && <div className="error-banner" role="alert" style={{ marginTop: 8 }}>{nmsError}</div>}
      {nmsResult && (
        <div role="status" style={{ marginTop: 10 }}>
          <div style={{ fontWeight: 700, color: nmsResult.success ? "#059669" : "#dc2626" }}>
            NMS 서버 → {nmsResult.target}: {nmsResult.success ? "연결 성공" : "연결 실패"}
          </div>
          <pre className="diagnostic-output" style={{ marginTop: 8 }}>{nmsResult.output}</pre>
        </div>
      )}
      <hr style={{ margin: "18px 0", border: 0, borderTop: "1px solid var(--border, #e5e7eb)" }} />
      <h2>From → To Ping 연결 확인</h2>
      <select
        aria-label="Ping 실행 From To"
        value={selection}
        onChange={(e) => {
          setSelection(e.target.value);
          setResult(null);
          setError(null);
        }}
        style={{ width: "100%", marginBottom: 8 }}
      >
        {links.flatMap((item) => [
          <option key={`${item.id}:FORWARD`} value={`${item.id}:FORWARD`}>
            {nodeLabel(item.src_device_id)} ({item.src_port ?? "?"}) → {nodeLabel(item.dst_device_id)} ({item.dst_port ?? "?"})
          </option>,
          <option key={`${item.id}:REVERSE`} value={`${item.id}:REVERSE`}>
            {nodeLabel(item.dst_device_id)} ({item.dst_port ?? "?"}) → {nodeLabel(item.src_device_id)} ({item.src_port ?? "?"})
          </option>,
        ])}
      </select>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 90px", gap: 8, marginBottom: 8 }}>
        <select
          aria-label="CLI 접속 프로토콜"
          value={protocol}
          onChange={(e) => {
            const next = e.target.value as "SSH" | "TELNET";
            setProtocol(next);
            setPort(next === "TELNET" ? 23 : 22);
          }}
        >
          <option value="SSH">SSH</option>
          <option value="TELNET">Telnet</option>
        </select>
        <input type="number" min={1} max={65535} aria-label="CLI 접속 포트" title="접속 포트" value={port} onChange={(e) => setPort(Number(e.target.value))} />
      </div>
      <input type="text" aria-label="CLI ID" placeholder="ID" value={username} onChange={(e) => setUsername(e.target.value)} style={{ width: "100%", marginBottom: 8 }} />
      <input
        type="password"
        aria-label="CLI Password"
        placeholder="Password (비우면 장비별 기본값 사용)"
        value={password}
        onChange={(e) => setPassword(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter") run(); }}
        style={{ width: "100%", marginBottom: 8 }}
      />
      {!password && <div className="muted" style={{ fontSize: 12, marginBottom: 8 }}>Password가 비어 있어 {defaultName}을 사용합니다.</div>}
      <button className="btn btn-primary" style={{ width: "100%" }} disabled={running} onClick={run}>
        {running ? "From → To Ping 실행 중..." : "From → To Ping으로 연결 확인"}
      </button>
      {error && <div className="error-banner" style={{ marginTop: 8 }}>{error}</div>}
      {result && (
        <div style={{ marginTop: 10 }}>
          <div style={{ fontWeight: 700, color: result.success ? "#059669" : "#dc2626" }}>
            {result.from_ip} → {result.to_ip}: {result.success ? "연결 성공" : "연결 실패"}
          </div>
          <div className="muted" style={{ marginTop: 4 }}>접속 방식: {result.protocol ?? protocol}</div>
          {!result.supported && <div className="muted" style={{ marginTop: 4 }}>원격 Ping을 실행할 수 없습니다.</div>}
          {result.packet_loss_percent != null && <div className="muted" style={{ marginTop: 4 }}>패킷 손실률: {result.packet_loss_percent}%</div>}
          {result.rtt_avg_ms != null && <div className="muted">RTT 최소/평균/최대: {result.rtt_min_ms} / {result.rtt_avg_ms} / {result.rtt_max_ms} ms</div>}
          {result.command && <div className="muted" style={{ marginTop: 4 }}>명령: {result.command}</div>}
          <pre className="diagnostic-output" style={{ marginTop: 8 }}>{result.output}</pre>
        </div>
      )}
    </section>
  );
}
