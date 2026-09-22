import { useEffect, useMemo, useState } from "react";
import { api, CredentialProfile, SystemInfo } from "../api/client";
import Pagination from "../components/Pagination";
import { formatUtcDateTime } from "../lib/dateTime";

// 18.1절: Credential Profile 관리(암호화 저장, 평문 재노출 금지) + 읽기 전용
// 시스템 정보. Credential 값(community)은 생성 후 절대 다시 보여주지 않는다.
export default function SettingsPage() {
  const [profiles, setProfiles] = useState<CredentialProfile[]>([]);
  const [systemInfo, setSystemInfo] = useState<SystemInfo | null>(null);
  const [name, setName] = useState("");
  const [community, setCommunity] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [profilesPage, setProfilesPage] = useState(1);
  const [profilesPageSize, setProfilesPageSize] = useState(20);
  const [selectedProfileIds, setSelectedProfileIds] = useState<Set<number>>(new Set());
  const [sshProfileId, setSshProfileId] = useState<number | null>(null);
  const [sshUsername, setSshUsername] = useState("");
  const [sshPassword, setSshPassword] = useState("");
  const [sshPort, setSshPort] = useState(22);
  const [cliProtocol, setCliProtocol] = useState<"SSH" | "TELNET">("SSH");
  const [savingSsh, setSavingSsh] = useState(false);

  const loadProfiles = () => api.listCredentialProfiles().then(setProfiles).catch((e) => setError(e.message));

  useEffect(() => {
    loadProfiles();
    api.getSystemInfo().then(setSystemInfo).catch((e) => setError(e.message));
  }, []);

  const profilesTotalPages = Math.max(1, Math.ceil(profiles.length / profilesPageSize));
  useEffect(() => {
    if (profilesPage > profilesTotalPages) setProfilesPage(profilesTotalPages);
  }, [profilesPage, profilesTotalPages]);
  const pagedProfiles = useMemo(() => {
    const startIdx = (profilesPage - 1) * profilesPageSize;
    return profiles.slice(startIdx, startIdx + profilesPageSize);
  }, [profiles, profilesPage, profilesPageSize]);

  const handleCreate = async () => {
    if (!community.trim()) {
      setError("SNMP community를 입력하세요.");
      return;
    }
    setCreating(true);
    setError(null);
    try {
      await api.createCredentialProfile(community.trim(), name.trim() || undefined);
      setName("");
      setCommunity("");
      loadProfiles();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setCreating(false);
    }
  };

  const handleDelete = async (id: number) => {
    if (!window.confirm("이 Credential Profile을 삭제하시겠습니까? 참조 중인 장비는 참조가 해제됩니다.")) return;
    try {
      await api.deleteCredentialProfile(id);
      loadProfiles();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const toggleSelectProfile = (id: number) => {
    setSelectedProfileIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const allVisibleProfilesSelected = pagedProfiles.length > 0 && pagedProfiles.every((p) => selectedProfileIds.has(p.id));

  const toggleSelectAllProfiles = () => {
    setSelectedProfileIds((prev) => {
      const next = new Set(prev);
      if (allVisibleProfilesSelected) {
        pagedProfiles.forEach((p) => next.delete(p.id));
      } else {
        pagedProfiles.forEach((p) => next.add(p.id));
      }
      return next;
    });
  };

  const handleBulkDelete = async () => {
    if (selectedProfileIds.size === 0) return;
    if (!window.confirm(`선택한 ${selectedProfileIds.size}건의 Credential Profile을 삭제하시겠습니까? 참조 중인 장비는 참조가 해제됩니다.`)) return;
    try {
      await api.bulkDeleteCredentialProfiles(Array.from(selectedProfileIds));
      setSelectedProfileIds(new Set());
      loadProfiles();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleDeleteAll = async () => {
    if (profiles.length === 0) return;
    if (!window.confirm(`전체 ${profiles.length}건의 Credential Profile을 삭제하시겠습니까? 참조 중인 장비는 참조가 해제됩니다.`)) return;
    try {
      await api.deleteAllCredentialProfiles();
      setSelectedProfileIds(new Set());
      loadProfiles();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleSaveSsh = async () => {
    if (sshProfileId == null || !sshUsername.trim() || !sshPassword) {
      setError("Credential Profile, SSH 사용자명, 암호를 모두 입력하세요.");
      return;
    }
    setSavingSsh(true);
    setError(null);
    try {
      await api.updateCredentialProfileSsh(sshProfileId, sshUsername.trim(), sshPassword, sshPort, cliProtocol);
      setSshUsername("");
      setSshPassword("");
      await loadProfiles();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setSavingSsh(false);
    }
  };

  return (
    <div>
      <div className="page-header">
        <h1>Settings</h1>
      </div>
      {error && <div className="error-banner">{error}</div>}

      <div className="card">
        <div className="tree-group-title">Credential Profile</div>
        <p className="muted" style={{ fontSize: 13 }}>
          SNMP community는 이곳에서 암호화되어 저장되며, 생성 후에는 평문으로 다시 조회할 수 없습니다. Discovery
          시작 시 community를 직접 입력하면 동일한 값에 대해 자동으로 재사용되는 Profile이 생성됩니다.
        </p>
        <div className="toolbar">
          <button className="btn btn-danger" disabled={selectedProfileIds.size === 0} onClick={handleBulkDelete}>
            선택 삭제 ({selectedProfileIds.size})
          </button>
          <button className="btn btn-danger" disabled={profiles.length === 0} onClick={handleDeleteAll}>
            전체 삭제
          </button>
        </div>
        <table>
          <thead>
            <tr>
              <th>
                <input type="checkbox" checked={allVisibleProfilesSelected} onChange={toggleSelectAllProfiles} aria-label="전체 선택" />
              </th>
              <th>이름</th>
              <th>SNMP</th>
              <th>CLI 접속</th>
              <th>생성 시각</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {pagedProfiles.map((p) => (
              <tr key={p.id}>
                <td>
                  <input
                    type="checkbox"
                    checked={selectedProfileIds.has(p.id)}
                    onChange={() => toggleSelectProfile(p.id)}
                    aria-label={`${p.name} 선택`}
                  />
                </td>
                <td>{p.name}</td>
                <td>{p.has_snmp ? "설정됨" : "-"}</td>
                <td>{p.has_cli ? `${p.cli_protocol} (:${p.ssh_port})` : "미설정"}</td>
                <td className="muted">{formatUtcDateTime(p.created_at)}</td>
                <td>
                  <button className="btn" style={{ marginRight: 6 }} onClick={() => setSshProfileId(p.id)}>
                    CLI 설정
                  </button>
                  <button className="btn btn-danger" onClick={() => handleDelete(p.id)}>
                    삭제
                  </button>
                </td>
              </tr>
            ))}
            {profiles.length === 0 && (
              <tr>
                <td colSpan={6} className="muted">
                  등록된 Credential Profile이 없습니다.
                </td>
              </tr>
            )}
          </tbody>
        </table>
        <Pagination
          page={profilesPage}
          pageSize={profilesPageSize}
          totalItems={profiles.length}
          onPageChange={setProfilesPage}
          onPageSizeChange={(size) => {
            setProfilesPageSize(size);
            setProfilesPage(1);
          }}
        />

        <div className="toolbar" style={{ marginTop: 12 }}>
          <input type="text" placeholder="이름 (선택, 비우면 자동 생성)" value={name} onChange={(e) => setName(e.target.value)} />
          <input
            type="password"
            placeholder="SNMP community"
            value={community}
            onChange={(e) => setCommunity(e.target.value)}
          />
          <button className="btn btn-primary" disabled={creating} onClick={handleCreate}>
            {creating ? "생성 중..." : "새 Credential Profile 생성"}
          </button>
        </div>

        <div className="tree-group-title" style={{ marginTop: 20 }}>장비 CLI 설정</div>
        <p className="muted" style={{ fontSize: 13 }}>
          링크의 From→To Ping은 From 장비의 기본 또는 연결된 Credential Profile을 사용해 SSH/Telnet으로 실행됩니다. 사용자명과 암호는 암호화 저장되며 다시 표시되지 않습니다.
        </p>
        <div className="toolbar">
          <select value={sshProfileId ?? ""} onChange={(e) => setSshProfileId(e.target.value ? Number(e.target.value) : null)}>
            <option value="">Credential Profile 선택</option>
            {profiles.map((profile) => (
              <option key={profile.id} value={profile.id}>{profile.name}</option>
            ))}
          </select>
          <select
            value={cliProtocol}
            onChange={(e) => {
              const protocol = e.target.value as "SSH" | "TELNET";
              setCliProtocol(protocol);
              setSshPort(protocol === "TELNET" ? 23 : 22);
            }}
          >
            <option value="SSH">SSH</option>
            <option value="TELNET">Telnet</option>
          </select>
          <input type="text" placeholder="CLI 사용자명" value={sshUsername} onChange={(e) => setSshUsername(e.target.value)} />
          <input type="password" placeholder="CLI 암호" value={sshPassword} onChange={(e) => setSshPassword(e.target.value)} />
          <input
            type="number"
            min={1}
            max={65535}
            aria-label="SSH 포트"
            value={sshPort}
            onChange={(e) => setSshPort(Number(e.target.value))}
            style={{ width: 90 }}
          />
          <button className="btn btn-primary" disabled={savingSsh || sshProfileId == null} onClick={handleSaveSsh}>
            {savingSsh ? "저장 중..." : "CLI 정보 저장"}
          </button>
        </div>
      </div>

      <div className="card">
        <div className="tree-group-title">시스템 정보 (읽기 전용)</div>
        {systemInfo ? (
          <table>
            <tbody>
              <tr>
                <th>Schema Version</th>
                <td>{systemInfo.schema_version}</td>
              </tr>
              <tr>
                <th>DB 연결 정보</th>
                <td className="muted">{systemInfo.db_path}</td>
              </tr>
              <tr>
                <th>Max Seed Hosts</th>
                <td>{systemInfo.max_seed_hosts}</td>
              </tr>
              <tr>
                <th>Max Discovery Depth</th>
                <td>{systemInfo.max_discovery_depth}</td>
              </tr>
              <tr>
                <th>Classification Threshold / Review Floor</th>
                <td>
                  {systemInfo.classification_threshold} / {systemInfo.classification_review_floor}
                </td>
              </tr>
              <tr>
                <th>Core / Floor Score Threshold</th>
                <td>
                  {systemInfo.core_score_threshold} / {systemInfo.floor_score_threshold}
                </td>
              </tr>
              <tr>
                <th>Stale / Offline After (초)</th>
                <td>
                  {systemInfo.stale_after_seconds} / {systemInfo.offline_after_seconds}
                </td>
              </tr>
              <tr>
                <th>자동 Discovery</th>
                <td>
                  {systemInfo.auto_discovery_enabled
                    ? `사용 · ${systemInfo.auto_discovery_interval_seconds / 60}분 주기`
                    : "사용 안 함"}
                </td>
              </tr>
            </tbody>
          </table>
        ) : (
          <div className="muted">불러오는 중...</div>
        )}
      </div>
    </div>
  );
}
