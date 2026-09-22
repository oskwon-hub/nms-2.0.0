import { useEffect, useMemo, useState } from "react";
import { api, ControlLog, Device, DiscoveryRun } from "../api/client";
import StatusBadge from "../components/StatusBadge";
import Pagination from "../components/Pagination";
import { formatUtcDateTime } from "../lib/dateTime";

// 21장/18.1절: 재고 요약 + Discovery 실행 이력 + 제어 Audit Log를 한 화면에서 볼 수
// 있게 한다. 장기 시계열 집계(트래픽 등)는 별도 시계열 저장소가 필요한 범위라
// 다음 단계로 남겨두고(README 참고), 여기서는 이미 저장된 재고/이력/Audit 데이터를
// 집계해 보여준다.
export default function ReportsPage() {
  const [devices, setDevices] = useState<Device[]>([]);
  const [runs, setRuns] = useState<DiscoveryRun[]>([]);
  const [logs, setLogs] = useState<ControlLog[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [runsPage, setRunsPage] = useState(1);
  const [runsPageSize, setRunsPageSize] = useState(20);
  const [logsPage, setLogsPage] = useState(1);
  const [logsPageSize, setLogsPageSize] = useState(20);
  const [selectedRunIds, setSelectedRunIds] = useState<Set<number>>(new Set());
  const [runsSkipNotice, setRunsSkipNotice] = useState<string | null>(null);
  const [selectedLogIds, setSelectedLogIds] = useState<Set<number>>(new Set());

  const loadRuns = () => api.listDiscoveryRuns(500).then(setRuns).catch((e) => setError(e.message));
  const loadLogs = () => api.listControlLogs({ limit: 500 }).then(setLogs).catch((e) => setError(e.message));

  useEffect(() => {
    // [KOS20260921] 서버 offset 페이지네이션이 없으므로, 페이지 크기 옵션 중
    // 최대값(500)만큼 한번에 받아 프론트에서 슬라이싱한다.
    api.listDevices().then(setDevices).catch((e) => setError(e.message));
    loadRuns();
    loadLogs();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const runsTotalPages = Math.max(1, Math.ceil(runs.length / runsPageSize));
  useEffect(() => {
    if (runsPage > runsTotalPages) setRunsPage(runsTotalPages);
  }, [runsPage, runsTotalPages]);
  const pagedRuns = useMemo(() => {
    const startIdx = (runsPage - 1) * runsPageSize;
    return runs.slice(startIdx, startIdx + runsPageSize);
  }, [runs, runsPage, runsPageSize]);

  const logsTotalPages = Math.max(1, Math.ceil(logs.length / logsPageSize));
  useEffect(() => {
    if (logsPage > logsTotalPages) setLogsPage(logsTotalPages);
  }, [logsPage, logsTotalPages]);
  const pagedLogs = useMemo(() => {
    const startIdx = (logsPage - 1) * logsPageSize;
    return logs.slice(startIdx, startIdx + logsPageSize);
  }, [logs, logsPage, logsPageSize]);

  const toggleSelectRun = (id: number) => {
    setSelectedRunIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const allVisibleRunsSelected = pagedRuns.length > 0 && pagedRuns.every((r) => selectedRunIds.has(r.id));

  const toggleSelectAllRuns = () => {
    setSelectedRunIds((prev) => {
      const next = new Set(prev);
      if (allVisibleRunsSelected) {
        pagedRuns.forEach((r) => next.delete(r.id));
      } else {
        pagedRuns.forEach((r) => next.add(r.id));
      }
      return next;
    });
  };

  const handleBulkDeleteRuns = async () => {
    if (selectedRunIds.size === 0) return;
    if (!window.confirm(`선택한 ${selectedRunIds.size}건의 Discovery 실행 이력을 삭제하시겠습니까?`)) return;
    setError(null);
    setRunsSkipNotice(null);
    try {
      const result = await api.bulkDeleteDiscoveryRuns(Array.from(selectedRunIds));
      if (result.skipped.length > 0) setRunsSkipNotice(`${result.skipped.length}건은 실행 중이라 삭제되지 않았습니다.`);
      setSelectedRunIds(new Set());
      await loadRuns();
    } catch (err: any) {
      setError(err.message);
    }
  };

  const handleDeleteAllRuns = async () => {
    if (runs.length === 0) return;
    if (!window.confirm(`전체 ${runs.length}건의 Discovery 실행 이력을 삭제하시겠습니까?`)) return;
    setError(null);
    setRunsSkipNotice(null);
    try {
      const result = await api.deleteAllDiscoveryRuns();
      if (result.skipped.length > 0) setRunsSkipNotice(`${result.skipped.length}건은 실행 중이라 삭제되지 않았습니다.`);
      setSelectedRunIds(new Set());
      await loadRuns();
    } catch (err: any) {
      setError(err.message);
    }
  };

  const toggleSelectLog = (id: number) => {
    setSelectedLogIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const allVisibleLogsSelected = pagedLogs.length > 0 && pagedLogs.every((l) => selectedLogIds.has(l.id));

  const toggleSelectAllLogs = () => {
    setSelectedLogIds((prev) => {
      const next = new Set(prev);
      if (allVisibleLogsSelected) {
        pagedLogs.forEach((l) => next.delete(l.id));
      } else {
        pagedLogs.forEach((l) => next.add(l.id));
      }
      return next;
    });
  };

  const handleBulkDeleteLogs = async () => {
    if (selectedLogIds.size === 0) return;
    if (!window.confirm(`선택한 ${selectedLogIds.size}건의 제어 Audit Log를 삭제하시겠습니까?`)) return;
    setError(null);
    try {
      await api.bulkDeleteControlLogs(Array.from(selectedLogIds));
      setSelectedLogIds(new Set());
      await loadLogs();
    } catch (err: any) {
      setError(err.message);
    }
  };

  const handleDeleteAllLogs = async () => {
    if (logs.length === 0) return;
    if (!window.confirm(`전체 ${logs.length}건의 제어 Audit Log를 삭제하시겠습니까?`)) return;
    setError(null);
    try {
      await api.deleteAllControlLogs();
      setSelectedLogIds(new Set());
      await loadLogs();
    } catch (err: any) {
      setError(err.message);
    }
  };

  const countBy = <K extends string>(items: { [key: string]: any }[], key: string): Record<string, number> =>
    items.reduce((acc, item) => {
      const k = (item[key] ?? "UNKNOWN") as K;
      acc[k] = (acc[k] ?? 0) + 1;
      return acc;
    }, {} as Record<string, number>);

  const byType = countBy(devices, "device_type");
  const byRole = countBy(devices, "device_role");
  const byStatus = countBy(devices, "status");

  return (
    <div>
      <div className="page-header">
        <h1>Reports</h1>
      </div>
      {error && <div className="error-banner">{error}</div>}

      <div className="card">
        <div className="tree-group-title">장비 재고 요약 ({devices.length}대)</div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 16, marginTop: 12 }}>
          <SummaryList title="Device Type별" counts={byType} />
          <SummaryList title="Device Role별" counts={byRole} />
          <SummaryList title="Status별" counts={byStatus} />
        </div>
      </div>

      <div className="card">
        <div className="tree-group-title">Discovery 실행 이력 (최근 {runs.length}건)</div>
        <div className="toolbar">
          <button className="btn btn-danger" disabled={selectedRunIds.size === 0} onClick={handleBulkDeleteRuns}>
            선택 삭제 ({selectedRunIds.size})
          </button>
          <button className="btn btn-danger" disabled={runs.length === 0} onClick={handleDeleteAllRuns}>
            전체 삭제
          </button>
        </div>
        {runsSkipNotice && <div className="muted" style={{ fontSize: 12, marginBottom: 8 }}>{runsSkipNotice}</div>}
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
            {pagedRuns.map((run) => (
              <tr key={run.id}>
                <td>
                  <input
                    type="checkbox"
                    checked={selectedRunIds.has(run.id)}
                    onChange={() => toggleSelectRun(run.id)}
                    aria-label={`Run #${run.id} 선택`}
                  />
                </td>
                <td>#{run.id}</td>
                <td>{run.profile}</td>
                <td>
                  <StatusBadge status={run.status} />
                </td>
                <td className="muted">{formatUtcDateTime(run.started_at)}</td>
                <td>
                  {run.scanned_count} / {run.alive_count}
                </td>
                <td>{run.error_count}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <Pagination
          page={runsPage}
          pageSize={runsPageSize}
          totalItems={runs.length}
          onPageChange={setRunsPage}
          onPageSizeChange={(size) => {
            setRunsPageSize(size);
            setRunsPage(1);
          }}
        />
      </div>

      <div className="card">
        <div className="tree-group-title">제어 Audit Log (최근 {logs.length}건)</div>
        <div className="toolbar">
          <button className="btn btn-danger" disabled={selectedLogIds.size === 0} onClick={handleBulkDeleteLogs}>
            선택 삭제 ({selectedLogIds.size})
          </button>
          <button className="btn btn-danger" disabled={logs.length === 0} onClick={handleDeleteAllLogs}>
            전체 삭제
          </button>
        </div>
        <table>
          <thead>
            <tr>
              <th>
                <input type="checkbox" checked={allVisibleLogsSelected} onChange={toggleSelectAllLogs} aria-label="전체 선택" />
              </th>
              <th>시각</th>
              <th>Device</th>
              <th>Action</th>
              <th>Before → After</th>
              <th>결과</th>
              <th>수행자</th>
            </tr>
          </thead>
          <tbody>
            {pagedLogs.map((log) => (
              <tr key={log.id}>
                <td>
                  <input
                    type="checkbox"
                    checked={selectedLogIds.has(log.id)}
                    onChange={() => toggleSelectLog(log.id)}
                    aria-label={`Log #${log.id} 선택`}
                  />
                </td>
                <td className="muted">{formatUtcDateTime(log.created_at)}</td>
                <td>#{log.device_id}</td>
                <td>{log.action}</td>
                <td className="muted">
                  {log.before_value} → {log.after_value}
                </td>
                <td>
                  <span
                    className={`badge ${log.result === "SUCCESS" ? "badge-online" : log.result === "DENIED" ? "badge-stale" : "badge-offline"}`}
                  >
                    {log.result}
                  </span>
                </td>
                <td className="muted">{log.performed_by}</td>
              </tr>
            ))}
            {logs.length === 0 && (
              <tr>
                <td colSpan={7} className="muted">
                  기록된 제어 작업이 없습니다.
                </td>
              </tr>
            )}
          </tbody>
        </table>
        <Pagination
          page={logsPage}
          pageSize={logsPageSize}
          totalItems={logs.length}
          onPageChange={setLogsPage}
          onPageSizeChange={(size) => {
            setLogsPageSize(size);
            setLogsPage(1);
          }}
        />
      </div>
    </div>
  );
}

function SummaryList({ title, counts }: { title: string; counts: Record<string, number> }) {
  const entries = Object.entries(counts).sort((a, b) => b[1] - a[1]);
  return (
    <div>
      <div className="muted" style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>
        {title}
      </div>
      <table>
        <tbody>
          {entries.map(([key, value]) => (
            <tr key={key}>
              <td>{key}</td>
              <td style={{ textAlign: "right" }}>{value}</td>
            </tr>
          ))}
          {entries.length === 0 && (
            <tr>
              <td className="muted">데이터 없음</td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
