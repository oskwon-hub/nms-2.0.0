# New Topology — 분석 및 설계 문서

> 이 문서는 **분석/설계 단계 산출물**이다. 코드 구현은 포함하지 않으며, 기존
> `Topology` 메뉴(`TopologyGraphPage.tsx`/`TopologyGraphView.tsx` 등)는 이
> 설계의 대상이 아니다 — 상단 메뉴의 **`Topology` 오른쪽에 `New Topology`라는
> 별도 메뉴/페이지를 새로 추가**하는 방향으로, 기존 화면과 나란히 둔다.

## 1. 배경

사용자가 제시한 설계안(계층형 배치, 줌 레벨별 점진적 노출, 3-패널 레이아웃,
LAG/STP 링크 표현, 우클릭 메뉴, View 타입 전환 등)을 검토하고, **현재 코드에
실제로 있는 것과 없는 것을 정확히 구분**한 뒤, 무엇을 재사용하고 무엇을 새로
만들어야 하는지 정리한다.

## 2. 제안 설계 요약 (13개 포인트 그루핑)

| 그룹 | 제안 내용 |
|---|---|
| 배치 | Internet→Router→Core→Floor/Access→Endpoint 순 계층형 기본 배치 |
| 점진적 노출 | Zoom Level 1(건물별 요약) → 2(스위치 구조) → 3(포트/단말) |
| 화면 구조 | 좌측 Network Navigator / 중앙 Topology / 우측 Device Detail 3-패널 |
| 노드 표현 | 장비 타입별 아이콘 + 상태 색상(Green/Yellow/Red/Gray/Blue) |
| 링크 표현 | hover 시 Port/Speed/Utilization/Errors/Discard |
| LAG | 물리 링크 여러 개를 굵은 링크 하나로 묶고, 클릭 시 멤버 포트 분해 표시 |
| STP | Blocking 링크를 점선/다른 스타일로, 클릭 시 State/Role/VLAN/Root 표시 |
| 우측 패널 | Overview/Ports/VLAN/ARP/FDB/LLDP/STP/PoE/Events/Configuration 탭 |
| 포트 제어 | Topology에서 직접 On/Off 하지 않고 "노드 선택 → 포트 선택 → 제어" 다단계 |
| 우클릭 메뉴 | Ping/Traceroute/SSH/Rediscover/Show Upstream/Show Downstream 등 |
| 장애 전파 | 장애 노드 강조 + "영향받는 장치 N개(PC 12/AP 3/CCTV 8)" 집계 |
| 5-레벨 계층 | Network→Internet/Gateway→Core/Router/L3→Distribution/Floor→Access/AP→Endpoint |
| View 타입 | Physical / L2 / L3 / VLAN 전환 가능한 다중 뷰 |
| 좌표 저장 | 자동 탐지된 연결 관계와, 사용자가 옮긴 화면 좌표(layout)를 분리 저장 |

## 3. 현재 구현 상태 정밀 진단 (Gap Analysis)

조사는 실제 파일:라인을 확인해 진행했다. 추측이 아니라 코드 사실 기준이다.

| # | 제안 항목 | 현재 상태 | 근거 |
|---|---|---|---|
| 1 | 계층형 배치 | **부분 존재** — `tree-vertical`/`tree-horizontal` 레이아웃이 `ROLE_TIER`(Core→Distribution→Floor→Access→Endpoint) 기준으로 이미 있음. 다만 5개 레이아웃 알고리즘 중 하나일 뿐, "항상 이 순서로 보여주는 기본 화면"은 아님 | `topologyLayouts.ts` |
| 2 | 줌 레벨 점진적 노출 | **부분 존재, 2단계뿐** — CLOUD(Role 요약) ↔ FULL(전체) 토글만 있음. "건물별 요약"이라는 중간 단계는 없음(Building/Site 개념 자체가 DB에 없음, `physical_floor`만 있음) | `TopologyGraphPage.tsx:121` `ViewMode` |
| 3 | 3-패널(좌 Navigator/중앙 Graph/우 Detail) | **없음** — 장비 상세는 별도 페이지/모달이고, 상시 우측 패널이나 좌측 트리 내비게이터가 Topology 화면에 통합돼 있지 않음(트리는 `DevicesTreePage.tsx`라는 별도 화면) | grep 결과 없음 |
| 4 | 노드 타입별 아이콘 | **없음** — `device_role`별 색상 채움만 있고(`ROLE_COLOR`), `device_type`(router/switch/AP/camera/PC)은 텍스트로만 쓰이고 아이콘/모양에 매핑되지 않음 | `TopologyGraphView.tsx:180-202`, `RoleCloudNode.tsx` |
| 5 | STP 링크 스타일 | **이미 잘 구현돼 있음** — Blocking 링크는 회색+점선(`"3 3"`)+라벨에 "(STP Blocked)" 추가+애니메이션 정지, STP Root 노드는 금색 테두리+★, 토글/범례까지 있음 | `TopologyGraphView.tsx:76-79, 220, 226-227, 236, 241` |
| 6 | LAG 집계 링크 | **없음(순수 표시 트릭만)** — `is_trunk` 필드는 있으나 어디서도 `True`로 설정되지 않는 죽은 필드. `lag_group`/LACP 수집도 전혀 없음. 유일한 "LAG"는 포트 **이름 문자열**이 `LAG1`/`LAG 2` 패턴이면 라벨만 바꿔주는 정규식 트릭(`LAG_NAME_PATTERN`)뿐, 실제 여러 물리 링크를 묶는 로직이 아님 | `TopologyGraphView.tsx:61-65, 81-84`; `protected.py:31`(읽기만) |
| 7 | 우측 패널 탭 구성(Overview/Ports/VLAN/ARP/FDB/LLDP/STP/PoE/Events/Configuration) | **거의 존재** — `DeviceDetailContent.tsx`에 overview/interfaces/neighbors/fdb/arp/routes/poe/**history**(이번 세션에 추가) 탭이 이미 있음. VLAN 전용 탭은 없지만 interfaces 탭에 VLAN 열이 있고, 이번 세션에 VLAN Set 컨트롤도 추가됨 | `DeviceDetailContent.tsx:18` `Tab` 타입 |
| 8 | 포트 제어 다단계(그래프에서 직접 On/Off 금지) | **이미 그렇게 되어 있음** — 노드 클릭→상세 패널/모달→탭→버튼 구조라 그래프 위에서 바로 끄고 켤 수 없음 | `DeviceDetailContent.tsx` |
| 9 | 우클릭 메뉴 확장 | **최소 수준** — 전체 메뉴 항목이 딱 2개: 노드="링크 추가", 엣지="링크 삭제". Ping은 있지만(사이드 패널 별도 폼) 우클릭 메뉴엔 없음. Traceroute/SSH/Rediscover/Show Upstream/Show Downstream 항목 없음. `TopologyContextMenu.tsx` 자체는 범용 컴포넌트(항목을 props로 받음)라 확장은 쉬움 | `TopologyContextMenu.tsx`; `TopologyGraphView.tsx:1165-1184` |
| 10 | Show Upstream | **로직은 있으나 표현이 다름** — `upstreamPath`가 depth 기준으로 상위 경로를 계산해 사이드 패널에 `"상위 경로(Core 방향): A → B → C"` 형태의 **인라인 텍스트**로만 보여줌. 팝업/그래프 형태 아님 | `TopologyGraphView.tsx:673-701, 947-950` |
| 11 | Show Downstream | **로직도 표현도 이미 있음** — `filterTopologyByDeviceDescendants()`가 역할 계층상 하위인 이웃을 재귀로 모아 전체 서브그래프를 반환하고, "전체 보기"에서 노드 클릭 시 이 서브그래프를 담은 **그래프 팝업**으로 이미 표시 중(`"{장비} 하위 전체 ({count}대)"`) | `topologyRoleClouds.ts:188-222`; `TopologyGraphPage.tsx:347-352, 451-461` |
| 12 | 링크 hover 시 Speed/Utilization/Errors/Discard | **Speed만 저장, Utilization/Errors/Discard는 수집 자체가 없음** — `in_octets`/`out_octets`는 인터페이스 표에 누적 바이트로만 표시되고 그래프 엣지에는 전혀 쓰이지 않음. 에러/디스카드 카운터(`ifInErrors` 등)는 SNMP 수집 대상에 아예 없음 | `DeviceDetailContent.tsx:295-297`; `models.py:232-234` 주석 |
| 13 | 장애 전파("영향받는 장치 N개") | **백엔드 로직만 부분 존재, 화면 표시 없음** — Alarms의 `_find_down_upstream()`이 "이 장비 장애가 상위 장애 때문에 억제(Suppressed)돼야 하는가"는 판단하지만, "이 장애로 영향받는 하위 장치가 몇 개인지" 집계해 보여주는 기능은 없음 | `alarms.py` |
| 14 | View 타입 전환(Physical/L2/L3/VLAN) | **없음** — `ViewMode`는 CLOUD/FULL 두 가지뿐이고, 이건 "요약이냐 전체냐"이지 "어떤 근거로 그린 그래프냐"가 아님. 근거별(LLDP/FDB/ARP 등) 링크 show/hide 체크박스는 있지만 별도 뷰 타입은 아님 | `TopologyGraphPage.tsx:121` |
| 15 | 좌표 저장(연결 관계 vs 사용자가 옮긴 배치 분리) | **부분 존재, 저장소가 갈라져 있음** — 연결 관계 자체는 이미 `network_link`/`network_link_evidence` 테이블에 잘 분리돼 있음(설계안이 제안하는 구조와 사실상 같음). 문제는 **사용자가 옮긴 좌표**: CLOUD 뷰는 브라우저 `localStorage`(`nms.topologyCloudPositions.v3`)에만 저장되고(기기/브라우저마다 따로 놀고, 서버 DB에는 전혀 없음), FULL 뷰는 그마저도 없어 새로고침마다 레이아웃이 다시 계산됨. 백엔드에 `topology_layout` 같은 테이블 자체가 없음 | `TopologyGraphPage.tsx:144-183`; `TopologyGraphView.tsx:360, 373-374`; `models.py` 테이블 목록에 없음 |

### 요약

- **이미 충분히 좋은 것**: STP 링크 스타일, 포트 제어 다단계 안전장치, Show
  Downstream(서브그래프 팝업), 상세 패널 탭 구성, 연결 관계 DB 스키마.
- **로직은 있는데 표현만 바꾸면 되는 것**: Show Upstream(텍스트→그래프/팝업으로),
  우클릭 메뉴(항목 몇 개만 추가).
- **처음부터 새로 만들어야 하는 것**: 3-패널 레이아웃, 노드 타입별 아이콘,
  Building/Site 계층, LAG 실제 모델링(+LACP 수집), 트래픽 Utilization/Errors
  수집·표시, View 타입 전환(L2/L3/VLAN), 서버 측 레이아웃 좌표 저장, 장애 전파
  집계 표시.

## 4. New Topology 배치 방식

상단 메뉴 `Topology` 오른쪽에 `New Topology`를 추가한다.

```
Devices  Topology  [New Topology]  Discovery  구성 관리  Alarms  Reports  Settings
```

- 새 라우트(예: `/topology-new`), 새 페이지 컴포넌트(예: `NewTopologyPage.tsx`).
- 기존 `TopologyGraphPage.tsx`/`TopologyGraphView.tsx`/`topologyLayouts.ts`/
  `topologyRoleClouds.ts`는 **읽기 전용으로 재사용**(함수 재사용은 하되 수정하지
  않음)하거나, 필요하면 새 파일로 분기(fork)한다 — 기존 화면 동작에 영향을
  주지 않는 것이 최우선 제약이다.
- 데이터 소스(`NetworkDevice`/`DeviceInterface`/`NetworkLink`/
  `NetworkLinkEvidence`/`ConfigChangeLog`)는 기존 API를 그대로 쓴다. 새 화면
  전용 API가 필요한 부분(레이아웃 저장, LAG 그룹 등)만 신규 엔드포인트를 추가한다.

## 5. 데이터 모델 설계 (신규 필요분만)

기존 스키마(`network_device`, `device_interface`, `network_link`,
`network_link_evidence`)는 제안 설계의 "node/link 관계 저장" 요구사항과 이미
거의 같은 모양이라 **변경하지 않는다**. 아래는 New Topology를 위해 새로
필요한 것만 정리한다.

### 5.1 레이아웃 좌표 저장 (`topology_layout`, 신규 테이블)

```
topology_layout
  id            PK
  scope         VARCHAR   -- 'NEW_TOPOLOGY' 등, 화면별로 좌표 공간을 분리
  node_key      VARCHAR   -- 'device:123' 또는 'role:CORE_SWITCH' 등
  x, y          FLOAT
  updated_at    DATETIME
```

- 현재 CLOUD 뷰의 `localStorage` 방식과 근본적으로 다른 점: **서버 DB에
  저장**해 브라우저/PC가 바뀌어도 같은 배치를 본다(제안 설계의 "재탐색해도
  화면 배치가 흐트러지지 않는다"는 요구사항과 정확히 일치).
- `scope`를 두는 이유: 기존 Topology의 `localStorage` 좌표와 New Topology의
  좌표가 서로 다른 저장소를 쓰게 해, 기존 화면에 영향이 없게 한다.

### 5.2 LAG 모델링 (`lag_group`, 신규 테이블 + 컬렉터 확장)

```
lag_group
  id            PK
  device_id     FK -> network_device.id
  name          VARCHAR   -- 'Port-Channel10' 등
  speed_mbps    INTEGER   -- 멤버 합산 대역폭(표시용)

device_interface
  + lag_group_id  FK -> lag_group.id, nullable  (신규 컬럼)
```

- 이 테이블을 채우려면 **discovery 컬렉터에 LACP-MIB(dot3ad, 표준) 또는 벤더
  전용 MIB 수집을 새로 추가**해야 한다 — 지금은 이런 수집 자체가 전혀 없다
  (포트 이름 정규식 트릭뿐). 벤더마다 LACP MIB 지원 편차가 커서, 실제 장비로
  검증이 필요한 항목으로 별도 플래그한다(§7 리스크 참고).

### 5.3 트래픽 Utilization / Errors (컬렉터 확장, 스키마는 최소 추가)

- `ifInErrors`/`ifOutErrors`/`ifInDiscards`/`ifOutDiscards` OID를 신규 수집.
- Utilization(%)은 "카운터 증가량 ÷ 경과 시간 ÷ 링크 속도"로 계산해야 하는데,
  지금 discovery는 매번 최신값으로 **덮어쓰기**만 하고 이전 값을 순간적으로도
  들고 있지 않는다(`in_octets`/`out_octets`는 항상 "가장 최근 수집값"). 두
  시점 값을 비교하려면 최소한 "직전 수집값"을 잠깐 저장해 두는 로직이
  필요하다 — 큰 구조 변경은 아니지만 기존 discovery 루프를 건드려야 하므로
  Phase를 뒤로 둔다.

### 5.4 Building/Site 계층 (결정 필요)

- 제안 설계의 "Zoom Level 1: 건물별 요약"을 구현하려면 지금 없는 상위 계층
  개념이 필요하다. 두 가지 선택지가 있다:
  1. `network_device.sys_location` 텍스트를 파싱해 건물명을 추출(이미
     `physical_floor` 추출에 쓰는 `extract_floor_label()`과 유사한 방식).
  2. 별도 `site`/`building` 테이블을 만들고 운영자가 수동으로 장비를
     배정한다.
- 이건 **사용자 확인이 필요한 결정 지점**이다 — 사내망 규모가 "건물 여러
  개"를 실제로 나눠 볼 만큼 큰지, 아니면 지금처럼 Role 계층(Core/Distribution
  /Floor/Access)만으로 충분한지에 따라 답이 달라진다.

## 6. 화면/컴포넌트 구조 설계

```
NewTopologyPage.tsx (신규)
├─ NetworkNavigatorPanel (좌측, 신규)
│    DevicesTreePage.tsx의 Role별 그룹핑 로직을 발췌해 재사용
│    검색창 포함
├─ TopologyCanvas (중앙, 신규 - React Flow 재사용)
│    - Zoom Level 상태(1/2/3)에 따라 표시 노드 집합을 바꿈
│    - 노드: device_type -> 아이콘 매핑(신규 매핑 테이블)
│    - 엣지: 기존 BendableEdge 스타일 로직(STP 등) 재사용
│    - 우클릭 메뉴: TopologyContextMenu.tsx 재사용 + 항목 확장
│        (Ping/Traceroute는 이미 있는 DeviceDiagnostics 로직 재사용,
│         Show Upstream/Downstream은 upstreamPath/filterTopologyByDeviceDescendants 재사용)
└─ DeviceDetailSidePanel (우측, 신규 컨테이너)
     내부 콘텐츠는 기존 DeviceDetailContent.tsx를 그대로 삽입
     (기존 컴포넌트 수정 없이 재사용 - 상시 사이드 패널이라는 배치만 다름)
```

- 핵심 설계 원칙: **가능한 한 기존 컴포넌트/함수를 "재사용"하고, 새로
  필요한 것은 새 파일로 추가**한다. 기존 `TopologyGraphView.tsx` 내부를
  고치는 대신, 필요한 순수 함수(`filterTopologyByDeviceDescendants`,
  `upstreamPath` 계산 로직 등)를 그대로 import해 New Topology 쪽에서 쓴다.

## 7. 줌 레벨 / 드릴다운 전략

| Level | 보여주는 것 | 재사용 가능 로직 |
|---|---|---|
| 1 | 전체망 요약 (Role별, 그리고/또는 Building별) | 기존 `buildRoleCloudGraph()` 재사용 가능 |
| 2 | 특정 그룹 더블클릭 → 그 안의 스위치들 | 기존 `filterTopologyByRole()` 재사용 가능 |
| 3 | 특정 스위치 더블클릭 → 포트+연결 단말 | 기존 `filterTopologyByDeviceDescendants()` 재사용 가능 |

기존 Topology의 "구름 보기"가 사실상 Level 1과 매우 유사하고, "전체 보기 +
드릴다운 팝업"이 Level 2/3과 유사하다 — 완전히 새로 설계하기보다 **기존 필터
함수들을 조합해 하나의 3-Level 드릴다운 흐름으로 재구성**하는 쪽이 리스크가
낮다.

## 8. 단계별(Phase) 로드맵

| Phase | 범위 | 신규 작업량 |
|---|---|---|
| **1 (MVP)** | 새 메뉴/라우트/페이지 뼈대, 3-패널 레이아웃, 계층형 기본 배치(기존 tree-vertical 재사용), 우측 패널에 기존 DeviceDetailContent 삽입, 우클릭 메뉴에 Show Upstream(그래프 팝업으로 개선)/Show Downstream/Ping/Traceroute 노출 | 대부분 기존 로직 재조합, 신규 컴포넌트는 레이아웃 뼈대 위주 |
| **2** | 노드 타입별 아이콘, Zoom Level 1→2→3 드릴다운, `topology_layout` 테이블로 서버 측 좌표 저장 | 신규 아이콘 세트, 신규 API 2~3개 |
| **3** | LAG 모델링(LACP MIB 수집 + `lag_group` 테이블) + 집계 링크 렌더링 | 컬렉터 신규 개발, 실장비 검증 필요 |
| **4** | View 타입 전환(L2/L3/VLAN), Errors/Discards 수집 + Utilization 계산 | discovery 델타 계산 구조 변경 필요 |
| **5** | 장애 전파 시각화("영향받는 장치 N개") | 기존 `_find_down_upstream` 확장 |

Phase 1~2는 기존 자산 재사용 비중이 높아 리스크가 낮고, Phase 3~4는 새로운
SNMP 수집(LACP, 에러 카운터)이 필요해 실제 장비 검증 없이는 정확도를 보장하기
어렵다.

## 9. 리스크 및 확인 필요 사항

1. **Building/Site 계층 도입 여부** (§5.4) — 사용자 확인 필요.
2. **LAG/LACP 수집** — 벤더별 MIB 지원 편차가 커서, 이전 STP Designated
   Bridge 조사 때처럼 실제 장비 대상 `snmpwalk` 검증이 필요할 가능성이 높다.
3. **Utilization 계산을 위한 델타 값 보관** — discovery 루프가 "덮어쓰기"
   구조라 최소 변경이 필요하며, 기존 Topology(구 화면)의 트래픽 관련 표시에도
   영향이 없는지 확인 필요(다만 현재 구 화면도 utilization을 안 쓰므로 영향
   없음으로 판단됨).
4. **유지보수 이중화** — 두 Topology 화면을 병행하면 당분간 유사 로직이 두
   곳에 존재하게 된다(의도적 병행이므로 문제는 아니지만, 장기적으로 새
   화면으로 완전히 갈아탈지 계속 병행할지는 이후 결정 사항).

## 10. 다음 단계 제안

이 문서에서 정리한 Phase 1(MVP) 범위를 기준으로, 실제 화면 시안(와이어프레임)을
그려보는 것을 다음 단계로 제안한다 — 사용자가 원래 언급한 "그림으로 설계해서
UI 시안을 만들어 보는" 단계에 해당한다.
