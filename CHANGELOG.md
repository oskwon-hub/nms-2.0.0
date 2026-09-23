# Changelog

이 프로젝트의 주요 변경 사항을 기록합니다. 형식은
[Keep a Changelog](https://keepachangelog.com/ko/1.1.0/)를 따르고, 버전은
[Semantic Versioning](https://semver.org/lang/ko/)을 따릅니다.

## [2.1.0] - 2026-09-23

### 추가

- 구성 관리(Configuration Management): VLAN/STP Root/Role/관리 IP/OS·펌웨어
  버전의 변경을 자동 감지해 기록하는 구성 변경 이력(Configuration Change
  History)과 전용 화면.
- Alarms에 CONFIG 카테고리 추가 - 재탐색이 감지한 예기치 않은 구성 변경을
  WARNING으로, 운영자가 직접 실행한 변경은 INFO로 구분해 알린다.
- 구성 변경 이력에서 이전 값으로 되돌리는 복구 기능(포트 Admin 상태, VLAN,
  Role).
- 포트 VLAN(PVID) 변경, 포트 설명(Description) 변경 제어 기능(SNMP SET) - 기존
  포트/PoE Enable-Disable과 동일하게 보호 포트 확인과 Audit Log를 남긴다.

## [2.0.0] - 2026-09-22

### 추가

- SQLite/PostgreSQL 기반으로 재설계한 네트워크 자동 탐색(Discovery) 엔진과 장비
  분류(Classification) 로직.
- LLDP/CDP/STP Designated Bridge/FDB+ARP 상관관계 등 다중 근거 기반 토폴로지 링크
  추정 및 시각화(구름 보기/전체 보기, 자동/원/격자/트리 배치).
- 장비 상세 화면의 Ping/Traceroute(TCP/UDP/ICMP)/L2 경로 추정 진단 기능.
- PoE/포트 Shutdown 등 장비 제어와 보호 대상 장비/포트 관리.
- 알람/리포트 화면.
- 오픈소스 공개를 위한 README/CONTRIBUTING/CODE_OF_CONDUCT/SECURITY 문서 및
  GPLv2 라이선스 정비.
