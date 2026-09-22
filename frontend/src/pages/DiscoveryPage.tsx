import { useEffect, useMemo, useRef, useState } from "react";
import { api, DiscoveryRun, HOSTNAME_RULE_LABELS, ProfileScope } from "../api/client";
import StatusBadge from "../components/StatusBadge";
import Pagination from "../components/Pagination";
import { formatUtcDateTime } from "../lib/dateTime";

const HOSTNAME_RULES = ["DNS", "NETBIOS", "MDNS"];
// [KOS20260921] concurrency=16으로 실행했을 때 SQLite 쓰기 잠금 경합이 누적돼
// 백엔드 전체가 응답 불가 상태에 빠지는 것을 실사용 중 확인했다(discovery/
// engine.py, config.py 주석 참고). 근본 리팩터링 전까지 상한을 8로 낮춘다.
const MAX_CONCURRENCY = 8;

// 17.5절: Discovery 전용 화면. 실행 단위(Discovery Run)로 CIDR/Profile/Protocol을
// 선택하고, 실행 중에는 Scanned/Alive/SNMP/Switch/Camera/PC/Unknown 및 오류 수를
// 실시간으로 표시한다. 이력은 discovery_run 테이블에 항상 남으므로, 화면을 벗어났다
// 돌아와도(다른 메뉴 이동 포함) 마운트 시 이력을 다시 불러와 RUNNING 상태인 Run이
// 있으면 자동으로 그 Run의 폴링을 재개한다 - Discovery 자체는 서버의 백그라운드
// 태스크로 동작하므로 화면 이동으로 중단되지 않는다.
const PROFILES = ["LIGHT", "STANDARD", "DETAILED", "TEMPLATE"];
const POLL_INTERVAL_MS = 2000;

// [KOS20260921] Discovery 시작 폼 입력값을 localStorage에 저장해, 화면을
// 벗어났다 돌아오거나 브라우저를 새로고침/재시작해도 마지막으로 사용한 값이
// 유지되도록 한다(사용자 요청: "선택된 값은 창이 다시 열려도 유지"). 실행 중인
// Run 자체의 폴링 재개는 이미 discovery_run 이력 조회로 별도 처리된다.
const FORM_STORAGE_KEY = "nms.discoveryForm.v1";

interface StoredDiscoveryForm {
  seedTargets?: string;
  cidrs?: string;
  profile?: string;
  community?: string;
  concurrency?: number;
  hostnameRules?: string[];
}

function loadStoredForm(): StoredDiscoveryForm {
  try {
    const raw = window.localStorage.getItem(FORM_STORAGE_KEY);
    return raw ? (JSON.parse(raw) as StoredDiscoveryForm) : {};
  } catch {
    return {};
  }
}

export default function DiscoveryPage() {
  const [seedTargets, setSeedTargets] = useState(() => loadStoredForm().seedTargets ?? "");
  const [cidrs, setCidrs] = useState(() => loadStoredForm().cidrs ?? "");
  const [profile, setProfile] = useState(() => loadStoredForm().profile ?? "STANDARD");
  const [community, setCommunity] = useState(() => loadStoredForm().community ?? "public");
  const [concurrency, setConcurrency] = useState(() =>
    Math.min(MAX_CONCURRENCY, loadStoredForm().concurrency ?? 2)
  );
  const [hostnameRules, setHostnameRules] = useState<string[]>(() => loadStoredForm().hostnameRules ?? []);
  // [KOS20260922] "기존 링크 정보를 모두 지우고 새로 하기" - 되돌릴 수 없는
  // 파괴적인 옵션이라 매번 명시적으로 다시 체크해야 하도록 localStorage에는
  // 저장하지 않는다(항상 꺼진 상태로 시작).
  const [resetLinks, setResetLinks] = useState(false);
  const [resetDevices, setResetDevices] = useState(false);
  const [run, setRun] = useState<DiscoveryRun | null>(null);
  const [history, setHistory] = useState<DiscoveryRun[]>([]);
  const [suggestedSubnets, setSuggestedSubnets] = useState<string[]>([]);
  const [profileScopes, setProfileScopes] = useState<ProfileScope[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [historyPage, setHistoryPage] = useState(1);
  const [historyPageSize, setHistoryPageSize] = useState(20);
  const [selectedRunIds, setSelectedRunIds] = useState<Set<number>>(new Set());
  const [skipNotice, setSkipNotice] = useState<string | null>(null);
  const pollRef = useRef<number | null>(null);

  const stopPolling = () => {
    if (pollRef.current) {
      window.clearInterval(pollRef.current);
      pollRef.current = null;
    }
  };

  const loadHistory = async (): Promise<DiscoveryRun[]> => {
    try {
      // [KOS20260921] 서버는 offset 페이지네이션을 지원하지 않으므로, 페이지
      // 크기 옵션 중 가장 큰 값(500)만큼 한번에 받아 프론트에서 슬라이싱한다.
      const rows = await api.listDiscoveryRuns(500);
      setHistory(rows);
      return rows;
    } catch (err) {
      return [];
    }
  };

  const watch = (runId: number) => {
    stopPolling();
    pollRef.current = window.setInterval(async () => {
      try {
        const updated = await api.getDiscoveryRun(runId);
        setRun(updated);
        // [KOS20260921] 상단 Stat 카드(run)만 갱신하고 이력 테이블(history)의
        // 같은 행은 그대로 둬서, 스캔 진행 중에는 이력 목록의 SCANNED/ALIVE가
        // 멈춰 보이는 문제가 있었다 - 이미 받아온 updated로 해당 행만 patch한다.
        setHistory((prev) => prev.map((r) => (r.id === updated.id ? updated : r)));
        if (updated.status !== "RUNNING") {
          stopPolling();
          loadHistory();
        }
      } catch {
        // 폴링 중 일시 오류는 조용히 무시하고 다음 tick에서 재시도한다.
      }
    }, POLL_INTERVAL_MS);
  };

  // 마운트 시: 이력을 불러오고, 진행 중인 Run이 있으면 그 상태를 이어서 보여준다
  // (화면 이동 후 복귀 시나리오 포함).
  useEffect(() => {
    (async () => {
      const rows = await loadHistory();
      const running = rows.find((r) => r.status === "RUNNING");
      if (running) {
        setRun(running);
        watch(running.id);
      }
    })();
    api.getSeedSuggestions().then((r) => setSuggestedSubnets(r.subnets)).catch(() => {});
    api.getProfileScopes().then(setProfileScopes).catch(() => {});
    return stopPolling;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleStart = async () => {
    if (
      resetDevices &&
      !window.confirm(
        "기존 장비 정보를 모두 삭제합니다(연관된 링크/인터페이스/ARP/FDB 등도 함께 삭제됩니다). 되돌릴 수 없습니다. 계속할까요?",
      )
    ) {
      return;
    }
    if (
      !resetDevices &&
      resetLinks &&
      !window.confirm("기존 링크 정보를 모두 삭제하고 이번 Discovery 결과로 새로 만듭니다. 되돌릴 수 없습니다. 계속할까요?")
    ) {
      return;
    }
    setError(null);
    setStarting(true);
    try {
      const payload = {
        profile,
        community: community || undefined,
        seed_targets: seedTargets.trim() ? seedTargets.split(/[\s,]+/).filter(Boolean) : undefined,
        cidrs: cidrs.trim() ? cidrs.split(/[\s,]+/).filter(Boolean) : undefined,
        concurrency,
        hostname_rules: hostnameRules.length > 0 ? hostnameRules : undefined,
        reset_links: resetLinks || undefined,
        reset_devices: resetDevices || undefined,
      };
      const started = await api.startDiscovery(payload);
      setRun(started);
      setResetLinks(false);
      setResetDevices(false);
      watch(started.id);
      loadHistory();
    } catch (err: any) {
      setError(err.message);
      loadHistory();
    } finally {
      setStarting(false);
    }
  };

  const viewRun = (target: DiscoveryRun) => {
    setRun(target);
    if (target.status === "RUNNING") {
      watch(target.id);
    } else {
      stopPolling();
    }
  };

  const addSubnet = (subnet: string) => {
    setCidrs((prev) => (prev.trim() ? `${prev.trim()} ${subnet}` : subnet));
  };

  const toggleHostnameRule = (rule: string) => {
    setHostnameRules((prev) => (prev.includes(rule) ? prev.filter((r) => r !== rule) : [...prev, rule]));
  };

  useEffect(() => {
    const data: StoredDiscoveryForm = { seedTargets, cidrs, profile, community, concurrency, hostnameRules };
    try {
      window.localStorage.setItem(FORM_STORAGE_KEY, JSON.stringify(data));
    } catch {
      // localStorage를 쓸 수 없어도(프라이빗 모드 등) 화면 기능 자체는 계속 동작해야 하므로 무시한다.
    }
  }, [seedTargets, cidrs, profile, community, concurrency, hostnameRules]);

  const historyTotalPages = Math.max(1, Math.ceil(history.length / historyPageSize));
  useEffect(() => {
    // [KOS20260921] 이력 갱신으로 목록이 줄어들어 현재 페이지가 범위를 벗어나면
    // 빈 페이지를 그대로 보여주지 않고 마지막 페이지로 당겨온다.
    if (historyPage > historyTotalPages) setHistoryPage(historyTotalPages);
  }, [historyPage, historyTotalPages]);

  const pagedHistory = useMemo(() => {
    const startIdx = (historyPage - 1) * historyPageSize;
    return history.slice(startIdx, startIdx + historyPageSize);
  }, [history, historyPage, historyPageSize]);

  const toggleSelectRun = (id: number) => {
    setSelectedRunIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const allVisibleRunsSelected = pagedHistory.length > 0 && pagedHistory.every((r) => selectedRunIds.has(r.id));

  const toggleSelectAllRuns = () => {
    setSelectedRunIds((prev) => {
      const next = new Set(prev);
      if (allVisibleRunsSelected) {
        pagedHistory.forEach((r) => next.delete(r.id));
      } else {
        pagedHistory.forEach((r) => next.add(r.id));
      }
      return next;
    });
  };

  const reportSkipped = (result: { deleted: number[]; skipped: number[] }) => {
    // [KOS20260921] RUNNING 상태 Run은 삭제되지 않으므로, 건너뛴 건수를 사용자에게 알려준다.
    setSkipNotice(result.skipped.length > 0 ? `${result.skipped.length}건은 실행 중이라 삭제되지 않았습니다.` : null);
  };

  const handleBulkDeleteRuns = async () => {
    if (selectedRunIds.size === 0) return;
    if (!window.confirm(`선택한 ${selectedRunIds.size}건의 실행 이력을 삭제하시겠습니까?`)) return;
    setError(null);
    setSkipNotice(null);
    try {
      const result = await api.bulkDeleteDiscoveryRuns(Array.from(selectedRunIds));
      reportSkipped(result);
      setSelectedRunIds(new Set());
      await loadHistory();
    } catch (err: any) {
      setError(err.message);
    }
  };

  const handleDeleteAllRuns = async () => {
    if (history.length === 0) return;
    if (!window.confirm(`전체 ${history.length}건의 실행 이력을 삭제하시겠습니까?`)) return;
    setError(null);
    setSkipNotice(null);
    try {
      const result = await api.deleteAllDiscoveryRuns();
      reportSkipped(result);
      setSelectedRunIds(new Set());
      await loadHistory();
    } catch (err: any) {
      setError(err.message);
    }
  };

  const isRunning = run?.status === "RUNNING";
  const progressPercent =
    run && run.scanned_count > 0 ? Math.min(100, Math.round((run.alive_count / Math.max(run.scanned_count, 1)) * 100)) : 0;

  return (
    <div>
      <div className="page-header">
        <h1>Discovery</h1>
      </div>

      <div className="card">
        <div className="field-row">
          <label>Seed Targets (공백/쉼표 구분 IP 목록, 비우면 로컬 ARP 캐시 사용)</label>
          <textarea rows={2} value={seedTargets} onChange={(e) => setSeedTargets(e.target.value)} placeholder="예: 10.0.0.1 10.0.0.2" />
        </div>
        <div className="field-row">
          <label>또는 CIDR 대역 (예: 10.0.0.0/24)</label>
          <textarea rows={2} value={cidrs} onChange={(e) => setCidrs(e.target.value)} />
          {suggestedSubnets.length > 0 && (
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 4 }}>
              <span className="muted" style={{ fontSize: 12, alignSelf: "center" }}>
                감지된 로컬 서브넷:
              </span>
              {suggestedSubnets.map((subnet) => (
                <button key={subnet} className="btn" style={{ padding: "2px 8px", fontSize: 12 }} onClick={() => addSubnet(subnet)}>
                  + {subnet}
                </button>
              ))}
            </div>
          )}
        </div>
        <div className="toolbar">
          <div className="field-row" style={{ marginBottom: 0 }}>
            <label>Profile</label>
            <select value={profile} onChange={(e) => setProfile(e.target.value)}>
              {PROFILES.map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
          </div>
          <div className="field-row" style={{ marginBottom: 0 }}>
            <label>SNMP Community</label>
            <input type="text" value={community} onChange={(e) => setCommunity(e.target.value)} />
          </div>
          <div className="field-row" style={{ marginBottom: 0 }}>
            <label>동시 스캔 개수</label>
            <input
              type="number"
              min={1}
              max={MAX_CONCURRENCY}
              value={concurrency}
              onChange={(e) => setConcurrency(Math.max(1, Math.min(MAX_CONCURRENCY, parseInt(e.target.value, 10) || 1)))}
              style={{ width: 80 }}
            />
          </div>
          <button className="btn btn-primary" disabled={starting || isRunning} onClick={handleStart} style={{ alignSelf: "flex-end" }}>
            {starting ? "시작 중..." : isRunning ? "실행 중인 Run이 있습니다" : "Discovery 시작"}
          </button>
          <label
            style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 13, alignSelf: "flex-end", marginBottom: 8 }}
            title="체크하면 스캔을 시작하기 전에 기존 network_link를 전부 삭제하고, 이번 Discovery 결과로만 새로 만듭니다."
          >
            <input
              type="checkbox"
              checked={resetLinks}
              disabled={resetDevices}
              onChange={(e) => setResetLinks(e.target.checked)}
            />
            기존 링크 정보 삭제 후 시작
          </label>
          <label
            style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 13, alignSelf: "flex-end", marginBottom: 8, color: "var(--danger, #dc2626)" }}
            title="체크하면 스캔을 시작하기 전에 기존 장비를 전부 삭제합니다(연관된 링크/인터페이스/ARP/FDB/Route/PoE 정보도 함께 삭제됨). 전체 인벤토리를 이번 Discovery 결과로만 다시 채웁니다."
          >
            <input type="checkbox" checked={resetDevices} onChange={(e) => setResetDevices(e.target.checked)} />
            기존 장비 정보 모두 삭제 후 시작
          </label>
        </div>
        <div className="field-row">
          <label>Hostname 식별 보조 규칙 (SNMP sysName이 없을 때만 시도, 순서대로 첫 성공 사용)</label>
          <div style={{ display: "flex", gap: 14, flexWrap: "wrap" }}>
            {HOSTNAME_RULES.map((rule) => (
              <label key={rule} style={{ display: "flex", alignItems: "center", gap: 4, fontWeight: 400 }}>
                <input type="checkbox" checked={hostnameRules.includes(rule)} onChange={() => toggleHostnameRule(rule)} />
                {HOSTNAME_RULE_LABELS[rule] ?? rule}
              </label>
            ))}
          </div>
        </div>
        {error && <div className="error-banner">{error}</div>}
      </div>

      {profileScopes.length > 0 && (
        <div className="card">
          <div className="tree-group-title">Profile별 수집 프로토콜 (6.4절)</div>
          <p className="muted" style={{ fontSize: 13 }}>
            선택한 <strong>{profile}</strong> Profile로 Discovery를 실행하면 표시된 프로토콜만 수집됩니다. 체크되지
            않은 항목은 해당 데이터가 비어 있거나(예: PoE/VLAN) 이전 값을 덮어쓰지 않고 그대로 둡니다.
          </p>
          <div style={{ overflowX: "auto" }}>
            <table>
              <thead>
                <tr>
                  <th>프로토콜 / MIB</th>
                  {profileScopes.map((s) => (
                    <th
                      key={s.profile}
                      style={s.profile === profile ? { background: "var(--accent-bg, rgba(99,102,241,0.12))" } : undefined}
                    >
                      {s.profile}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {profileScopes[0].protocols.map((proto, idx) => (
                  // [KOS20260922] STP 행이 collect_interfaces와 같은 key를 공유해서
                  // (같은 조건으로 수집됨을 표현) proto.key만으로는 더 이상 유일하지
                  // 않다 - 행 순서가 고정된 정적 목록이므로 idx를 함께 쓴다.
                  <tr key={`${proto.key}-${idx}`}>
                    <td>{proto.label}</td>
                    {profileScopes.map((s) => (
                      <td
                        key={s.profile}
                        style={{
                          textAlign: "center",
                          ...(s.profile === profile ? { background: "var(--accent-bg, rgba(99,102,241,0.08))" } : {}),
                        }}
                      >
                        {s.protocols[idx].enabled ? "✓" : "—"}
                      </td>
                    ))}
                  </tr>
                ))}
                <tr>
                  <td>재귀 탐색(BFS로 이웃 장비까지 확장)</td>
                  {profileScopes.map((s) => (
                    <td
                      key={s.profile}
                      style={{
                        textAlign: "center",
                        ...(s.profile === profile ? { background: "var(--accent-bg, rgba(99,102,241,0.08))" } : {}),
                      }}
                    >
                      {s.expand_neighbors ? "✓" : "—"}
                    </td>
                  ))}
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      )}

      {run && (
        <div className="card">
          <div className="page-header">
            <h1 style={{ fontSize: 16 }}>
              Discovery Run #{run.id} <span className="muted">({run.profile})</span>
            </h1>
            <StatusBadge status={run.status} />
          </div>
          <div className="progress-bar">
            <div className="progress-bar-fill" style={{ width: `${run.status === "RUNNING" ? progressPercent : 100}%` }} />
          </div>
          {run.status === "RUNNING" && (
            <div className="muted" style={{ fontSize: 13, marginTop: 6 }}>
              현재 스캔 중 (동시 {run.concurrency}개):{" "}
              {run.current_ips && JSON.parse(run.current_ips).length > 0 ? (
                (JSON.parse(run.current_ips) as string[]).map((ip) => (
                  <code key={ip} style={{ marginRight: 6 }}>
                    {ip}
                  </code>
                ))
              ) : (
                <code>{run.current_ip ?? "-"}</code>
              )}
            </div>
          )}
          {run.hostname_rules && (
            <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>
              Hostname 규칙:{" "}
              {(JSON.parse(run.hostname_rules) as string[]).map((r) => HOSTNAME_RULE_LABELS[r] ?? r).join(" → ")}
            </div>
          )}
          <div className="stat-grid">
            <Stat label="Scanned" value={run.scanned_count} />
            <Stat label="Alive" value={run.alive_count} />
            <Stat label="SNMP" value={run.snmp_count} />
            <Stat label="Switch" value={run.switch_count} />
            <Stat label="Camera" value={run.camera_count} />
            <Stat label="PC" value={run.pc_count} />
            <Stat label="Unknown" value={run.unknown_count} />
            <Stat label="Errors" value={run.error_count} />
          </div>
        </div>
      )}

      <div className="card">
        <div className="tree-group-title">실행 이력</div>
        <div className="toolbar">
          <button className="btn btn-danger" disabled={selectedRunIds.size === 0} onClick={handleBulkDeleteRuns}>
            선택 삭제 ({selectedRunIds.size})
          </button>
          <button className="btn btn-danger" disabled={history.length === 0} onClick={handleDeleteAllRuns}>
            전체 삭제
          </button>
        </div>
        {skipNotice && <div className="muted" style={{ fontSize: 12, marginBottom: 8 }}>{skipNotice}</div>}
        {history.length === 0 && <div className="muted">아직 실행한 Discovery가 없습니다.</div>}
        {history.length > 0 && (
          <table>
            <thead>
              <tr>
                <th>
                  <input type="checkbox" checked={allVisibleRunsSelected} onChange={toggleSelectAllRuns} aria-label="전체 선택" />
                </th>
                <th>ID</th>
                <th>Profile</th>
                <th>상태</th>
                <th>시작 시각</th>
                <th>Scanned/Alive</th>
                <th>Errors</th>
              </tr>
            </thead>
            <tbody>
              {pagedHistory.map((row) => (
                <tr key={row.id} className="clickable" onClick={() => viewRun(row)}>
                  <td onClick={(e) => e.stopPropagation()}>
                    <input
                      type="checkbox"
                      checked={selectedRunIds.has(row.id)}
                      onChange={() => toggleSelectRun(row.id)}
                      aria-label={`Run #${row.id} 선택`}
                    />
                  </td>
                  <td>#{row.id}</td>
                  <td>{row.profile}</td>
                  <td>
                    <StatusBadge status={row.status} />
                  </td>
                  <td className="muted">{formatUtcDateTime(row.started_at)}</td>
                  <td>
                    {row.scanned_count} / {row.alive_count}
                  </td>
                  <td>{row.error_count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <Pagination
          page={historyPage}
          pageSize={historyPageSize}
          totalItems={history.length}
          onPageChange={setHistoryPage}
          onPageSizeChange={(size) => {
            setHistoryPageSize(size);
            setHistoryPage(1);
          }}
        />
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="stat-tile">
      <div className="value">{value}</div>
      <div className="label">{label}</div>
    </div>
  );
}
