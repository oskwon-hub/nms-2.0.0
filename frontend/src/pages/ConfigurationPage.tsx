import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, ConfigChangeLog, Device } from "../api/client";
import Pagination from "../components/Pagination";
import { formatUtcDateTime } from "../lib/dateTime";

// [KOS20260923] "구성 관리" 메뉴 요청 - 지금은 변경 이력 탭만 있지만, 기준
// 구성·드리프트 등 이후 항목이 추가될 자리라 탭 구조를 미리 둔다.
type ConfigTab = "history";

// device_id -> "hostname(ip)" 같은 사람이 읽을 수 있는 라벨로 바꿔 보여주기 위한
// 조회 맵. Reports 화면의 Control Log 표(#device_id만 표시)보다 알아보기
// 쉽게, 장비 상세로 바로 이동할 수 있는 링크도 함께 제공한다.
function deviceLabel(devices: Map<number, Device>, deviceId: number): string {
  const d = devices.get(deviceId);
  if (!d) return `#${deviceId}`;
  return d.hostname || d.management_ip || `#${deviceId}`;
}

// 필드 이름을 화면에 그대로 노출하지 않고 한글 라벨로 바꾼다 - 매핑에 없는
// 필드가 나중에 추가돼도(예: 새 구성 항목) 원래 이름 그대로 보여주면 되므로
// 목록을 하드코딩해도 깨지지 않는다.
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

// backend/app/control/config_restore.py의 RESTORABLE_FIELDS와 맞춘다 - 실제
// 장비에 안전하게 되돌릴 SET 경로가 있거나(admin_status) NMS 내부 판정값이라
// DB만 바꾸면 되는(device_role) 필드만 "복구" 버튼을 보여준다. 나머지는 눌러도
// 어차피 서버가 거부하므로, 처음부터 버튼을 숨겨 혼란을 줄인다.
const RESTORABLE_FIELDS = new Set(["admin_status", "device_role"]);

export default function ConfigurationPage() {
  const [tab] = useState<ConfigTab>("history");
  const [devices, setDevices] = useState<Device[]>([]);
  const [logs, setLogs] = useState<ConfigChangeLog[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [deviceFilter, setDeviceFilter] = useState("");
  const [sourceFilter, setSourceFilter] = useState<"" | "DISCOVERY" | "MANUAL">("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());

  const deviceById = useMemo(() => new Map(devices.map((d) => [d.id, d])), [devices]);

  const loadLogs = () => api.listConfigChanges({ limit: 500 }).then(setLogs).catch((e) => setError(e.message));

  useEffect(() => {
    api.listDevices().then(setDevices).catch((e) => setError(e.message));
    loadLogs();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const filteredLogs = useMemo(() => {
    return logs.filter((log) => {
      if (sourceFilter && log.source !== sourceFilter) return false;
      if (deviceFilter) {
        const label = deviceLabel(deviceById, log.device_id).toLowerCase();
        if (!label.includes(deviceFilter.toLowerCase()) && !String(log.device_id).includes(deviceFilter)) {
          return false;
        }
      }
      return true;
    });
  }, [logs, sourceFilter, deviceFilter, deviceById]);

  const totalPages = Math.max(1, Math.ceil(filteredLogs.length / pageSize));
  useEffect(() => {
    if (page > totalPages) setPage(totalPages);
  }, [page, totalPages]);
  const pagedLogs = useMemo(() => {
    const startIdx = (page - 1) * pageSize;
    return filteredLogs.slice(startIdx, startIdx + pageSize);
  }, [filteredLogs, page, pageSize]);

  const allVisibleSelected = pagedLogs.length > 0 && pagedLogs.every((l) => selectedIds.has(l.id));
  const toggleSelectAll = () => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (allVisibleSelected) pagedLogs.forEach((l) => next.delete(l.id));
      else pagedLogs.forEach((l) => next.add(l.id));
      return next;
    });
  };
  const toggleSelect = (id: number) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const handleBulkDelete = async () => {
    if (!window.confirm(`선택한 ${selectedIds.size}건을 삭제할까요?`)) return;
    await api.bulkDeleteConfigChanges(Array.from(selectedIds));
    setSelectedIds(new Set());
    loadLogs();
  };
  const handleDeleteAll = async () => {
    if (!window.confirm("전체 변경 이력을 삭제할까요? 되돌릴 수 없습니다.")) return;
    await api.deleteAllConfigChanges();
    setSelectedIds(new Set());
    loadLogs();
  };

  const [restoringId, setRestoringId] = useState<number | null>(null);
  const handleRestore = async (log: ConfigChangeLog) => {
    const label = deviceLabel(deviceById, log.device_id);
    if (!window.confirm(`${label}의 ${fieldLabel(log.field_name)}을(를) "${log.old_value ?? "(없음)"}"(으)로 복구할까요?`)) {
      return;
    }
    setRestoringId(log.id);
    setError(null);
    try {
      const result = await api.restoreConfigChange(log.id);
      if (result.result !== "SUCCESS") {
        setError(result.error_message || `복구 실패 (${result.result})`);
      }
      loadLogs();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setRestoringId(null);
    }
  };

  return (
    <div>
      <div className="page-header">
        <h1>구성 관리</h1>
        <span className="muted">{filteredLogs.length}건</span>
      </div>
      {error && <div className="error-banner">{error}</div>}

      <div className="toolbar">
        <button className={tab === "history" ? "btn btn-primary" : "btn"}>변경 이력</button>
      </div>

      {tab === "history" && (
        <div className="card">
          <span className="muted" style={{ fontSize: 12 }}>
            NMS 제어(포트/PoE)가 아닌 구성 필드(VLAN, STP Root, Role, 관리 IP, 펌웨어 등)의 변경을 보여줍니다.
            "재탐색 감지"는 다음 재탐색 때 장비 쪽 값이 이전과 달라진 것을 발견한 경우, "수동 변경"은 운영자가
            API로 직접 값을 바꾼 경우입니다.
          </span>

          <div className="toolbar" style={{ marginTop: 10 }}>
            <input
              placeholder="장비 검색 (hostname/IP/ID)"
              value={deviceFilter}
              onChange={(e) => {
                setDeviceFilter(e.target.value);
                setPage(1);
              }}
              style={{ width: 220 }}
            />
            <select
              value={sourceFilter}
              onChange={(e) => {
                setSourceFilter(e.target.value as "" | "DISCOVERY" | "MANUAL");
                setPage(1);
              }}
            >
              <option value="">전체 주체</option>
              <option value="DISCOVERY">재탐색 감지</option>
              <option value="MANUAL">수동 변경</option>
            </select>
            <button className="btn btn-danger" disabled={selectedIds.size === 0} onClick={handleBulkDelete}>
              선택 삭제 ({selectedIds.size})
            </button>
            <button className="btn btn-danger" disabled={logs.length === 0} onClick={handleDeleteAll}>
              전체 삭제
            </button>
          </div>

          <table>
            <thead>
              <tr>
                <th>
                  <input type="checkbox" checked={allVisibleSelected} onChange={toggleSelectAll} aria-label="전체 선택" />
                </th>
                <th>시각</th>
                <th>장비</th>
                <th>필드</th>
                <th>이전 → 새 값</th>
                <th>주체</th>
                <th>수행자</th>
                <th>복구</th>
              </tr>
            </thead>
            <tbody>
              {pagedLogs.map((log) => (
                <tr key={log.id}>
                  <td>
                    <input
                      type="checkbox"
                      checked={selectedIds.has(log.id)}
                      onChange={() => toggleSelect(log.id)}
                      aria-label={`변경 #${log.id} 선택`}
                    />
                  </td>
                  <td className="muted">{formatUtcDateTime(log.detected_at)}</td>
                  <td>
                    <Link to={`/devices/${log.device_id}`}>{deviceLabel(deviceById, log.device_id)}</Link>
                  </td>
                  <td>{fieldLabel(log.field_name)}</td>
                  <td className="muted">
                    {log.old_value ?? "(없음)"} → {log.new_value ?? "(없음)"}
                  </td>
                  <td>
                    <span className={`badge ${log.source === "MANUAL" ? "badge-stale" : "badge-online"}`}>
                      {log.source === "MANUAL" ? "수동 변경" : "재탐색 감지"}
                    </span>
                  </td>
                  <td className="muted">{log.performed_by ?? "-"}</td>
                  <td>
                    {RESTORABLE_FIELDS.has(log.field_name) ? (
                      <button
                        className="btn"
                        disabled={restoringId === log.id}
                        onClick={() => handleRestore(log)}
                      >
                        {restoringId === log.id ? "복구 중..." : "이전 값으로 복구"}
                      </button>
                    ) : (
                      <span className="muted">지원 안 함</span>
                    )}
                  </td>
                </tr>
              ))}
              {filteredLogs.length === 0 && (
                <tr>
                  <td colSpan={8} className="muted">
                    기록된 구성 변경이 없습니다.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
          <Pagination
            page={page}
            pageSize={pageSize}
            totalItems={filteredLogs.length}
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
