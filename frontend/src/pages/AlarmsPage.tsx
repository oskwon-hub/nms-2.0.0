import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Alarm, api } from "../api/client";
import Pagination from "../components/Pagination";
import { formatUtcDateTime, parseUtcDateTime } from "../lib/dateTime";

// 18.1/12.1절: 전용 event/alarm 이력 테이블 없이, 장비/링크 status·제어 Audit
// Log·Discovery 실패 등 이미 저장된 현재 상태에서 파생한 "현재 활성 알람" 목록이다.
// 12.1절 Dependency 억제: 상위 장비 장애로 인한 하위 장비 알람은 Suppressed로 표시된다.
const SEVERITY_ORDER: Record<string, number> = { CRITICAL: 0, WARNING: 1, INFO: 2 };
const SEVERITY_CLASS: Record<string, string> = {
  CRITICAL: "badge-offline",
  WARNING: "badge-stale",
  INFO: "badge-running",
};
const CATEGORY_LABEL: Record<string, string> = {
  DEVICE: "장비",
  LINK: "링크",
  CONTROL: "제어",
  DISCOVERY: "Discovery",
  CONFIG: "구성 변경",
};

const POLL_INTERVAL_MS = 15000;

export default function AlarmsPage() {
  const [alarms, setAlarms] = useState<Alarm[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [severityFilter, setSeverityFilter] = useState("");
  const [categoryFilter, setCategoryFilter] = useState("");
  const [showSuppressed, setShowSuppressed] = useState(true);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [selectedAlarmIds, setSelectedAlarmIds] = useState<Set<string>>(new Set());
  const navigate = useNavigate();

  const load = () => {
    api
      .listAlarms({
        severity: severityFilter || undefined,
        category: categoryFilter || undefined,
        include_suppressed: showSuppressed,
      })
      .then((data) => {
        setAlarms(data);
        setError(null);
      })
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    setLoading(true);
    load();
    const timer = window.setInterval(load, POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [severityFilter, categoryFilter, showSuppressed]);

  const counts = alarms.reduce(
    (acc, a) => {
      if (!a.suppressed) acc[a.severity] = (acc[a.severity] ?? 0) + 1;
      return acc;
    },
    {} as Record<string, number>,
  );
  const suppressedCount = alarms.filter((a) => a.suppressed).length;

  const sorted = [...alarms].sort((a, b) => {
    if (a.suppressed !== b.suppressed) return a.suppressed ? 1 : -1;
    const sevDiff = (SEVERITY_ORDER[a.severity] ?? 9) - (SEVERITY_ORDER[b.severity] ?? 9);
    if (sevDiff !== 0) return sevDiff;
    return parseUtcDateTime(b.occurred_at).getTime() - parseUtcDateTime(a.occurred_at).getTime();
  });

  const goToRelated = (alarm: Alarm) => {
    if (alarm.device_id) navigate(`/devices/${alarm.device_id}`);
  };

  const totalPages = Math.max(1, Math.ceil(sorted.length / pageSize));
  useEffect(() => {
    // [KOS20260921] 15초마다 폴링되므로, 사용자가 페이지를 넘긴 상태에서 목록이
    // 갱신돼도 1페이지로 강제 이동시키지 않는다. 목록이 줄어 현재 페이지가
    // 범위를 벗어났을 때만 마지막 페이지로 당겨온다.
    if (page > totalPages) setPage(totalPages);
  }, [page, totalPages]);

  const paged = useMemo(() => {
    const startIdx = (page - 1) * pageSize;
    return sorted.slice(startIdx, startIdx + pageSize);
  }, [sorted, page, pageSize]);

  const toggleSelectAlarm = (id: string) => {
    setSelectedAlarmIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const allVisibleAlarmsSelected = paged.length > 0 && paged.every((a) => selectedAlarmIds.has(a.id));

  const toggleSelectAllAlarms = () => {
    setSelectedAlarmIds((prev) => {
      const next = new Set(prev);
      if (allVisibleAlarmsSelected) {
        paged.forEach((a) => next.delete(a.id));
      } else {
        paged.forEach((a) => next.add(a.id));
      }
      return next;
    });
  };

  const handleDismissSelected = async () => {
    if (selectedAlarmIds.size === 0) return;
    if (!window.confirm(`선택한 ${selectedAlarmIds.size}건의 알람을 삭제하시겠습니까? (동일 조건이 다시 발생하면 알람이 다시 나타날 수 있습니다)`)) return;
    setError(null);
    try {
      const targets = alarms.filter((a) => selectedAlarmIds.has(a.id)).map((a) => ({ id: a.id, occurred_at: a.occurred_at }));
      await api.dismissAlarms(targets);
      setSelectedAlarmIds(new Set());
      load();
    } catch (err: any) {
      setError(err.message);
    }
  };

  const handleDismissAll = async () => {
    if (sorted.length === 0) return;
    if (!window.confirm(`전체 ${sorted.length}건의 알람을 삭제하시겠습니까? (동일 조건이 다시 발생하면 알람이 다시 나타날 수 있습니다)`)) return;
    setError(null);
    try {
      await api.dismissAllAlarms({
        severity: severityFilter || undefined,
        category: categoryFilter || undefined,
        include_suppressed: showSuppressed,
      });
      setSelectedAlarmIds(new Set());
      load();
    } catch (err: any) {
      setError(err.message);
    }
  };

  return (
    <div>
      <div className="page-header">
        <h1>Alarms</h1>
      </div>

      <div className="stat-grid">
        <Stat label="Critical" value={counts.CRITICAL ?? 0} />
        <Stat label="Warning" value={counts.WARNING ?? 0} />
        <Stat label="Info" value={counts.INFO ?? 0} />
        <Stat label="Suppressed" value={suppressedCount} />
      </div>

      <div className="toolbar">
        <select value={severityFilter} onChange={(e) => setSeverityFilter(e.target.value)}>
          <option value="">전체 심각도</option>
          <option value="CRITICAL">CRITICAL</option>
          <option value="WARNING">WARNING</option>
          <option value="INFO">INFO</option>
        </select>
        <select value={categoryFilter} onChange={(e) => setCategoryFilter(e.target.value)}>
          <option value="">전체 분류</option>
          <option value="DEVICE">장비</option>
          <option value="LINK">링크</option>
          <option value="CONTROL">제어</option>
          <option value="DISCOVERY">Discovery</option>
          <option value="CONFIG">구성 변경</option>
        </select>
        <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 13 }}>
          <input type="checkbox" checked={showSuppressed} onChange={(e) => setShowSuppressed(e.target.checked)} />
          억제된 알람 표시
        </label>
        <button className="btn" onClick={load}>
          새로고침
        </button>
        <button className="btn btn-danger" disabled={selectedAlarmIds.size === 0} onClick={handleDismissSelected}>
          선택 삭제 ({selectedAlarmIds.size})
        </button>
        <button className="btn btn-danger" disabled={sorted.length === 0} onClick={handleDismissAll}>
          전체 삭제
        </button>
      </div>

      {error && <div className="error-banner">{error}</div>}
      {loading && <div className="muted">불러오는 중...</div>}

      {!loading && sorted.length === 0 && <div className="card muted">현재 활성 알람이 없습니다.</div>}

      {!loading && sorted.length > 0 && (
        <div className="card">
          <table>
            <thead>
              <tr>
                <th>
                  <input type="checkbox" checked={allVisibleAlarmsSelected} onChange={toggleSelectAllAlarms} aria-label="전체 선택" />
                </th>
                <th>심각도</th>
                <th>분류</th>
                <th>메시지</th>
                <th>발생 시각</th>
                <th>비고</th>
              </tr>
            </thead>
            <tbody>
              {paged.map((alarm) => (
                <tr
                  key={alarm.id}
                  className={alarm.device_id ? "clickable" : ""}
                  onClick={() => goToRelated(alarm)}
                  style={{ opacity: alarm.suppressed ? 0.55 : 1 }}
                >
                  <td onClick={(e) => e.stopPropagation()}>
                    <input
                      type="checkbox"
                      checked={selectedAlarmIds.has(alarm.id)}
                      onChange={() => toggleSelectAlarm(alarm.id)}
                      aria-label="알람 선택"
                    />
                  </td>
                  <td>
                    <span className={`badge ${SEVERITY_CLASS[alarm.severity] ?? "badge-unknown"}`}>{alarm.severity}</span>
                  </td>
                  <td>{CATEGORY_LABEL[alarm.category] ?? alarm.category}</td>
                  <td>{alarm.message}</td>
                  <td className="muted">{formatUtcDateTime(alarm.occurred_at)}</td>
                  <td className="muted">
                    {alarm.suppressed ? `Suppressed - ${alarm.suppressed_reason}` : ""}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <Pagination
            page={page}
            pageSize={pageSize}
            totalItems={sorted.length}
            onPageChange={setPage}
            onPageSizeChange={(size) => {
              setPageSize(size);
              setPage(1);
            }}
          />
        </div>
      )}
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
