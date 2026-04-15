# keyword_filter — 프로젝트 지침
> 버전: v1.0 | 최종 수정: 2026-04-16

## 이 프로젝트가 하는 일
네이버/커뮤니티 키워드 필터 검색기. 매일 새벽 1시 자동 수집 → 신규 게시글 폴더 + 원고.txt 자동 생성 → 텔레그램 알림.

## 실행 구조
- 메인: `auto_collect.py` (헤드리스, launchd 실행)
- UI: `keyword_filter.py` + `electron/` (데스크탑 앱)
- launchd: `com.nomos.keyword-filter` (매일 01:00)
- 설정: `config.json`

## 환경 설정
- 텔레그램: 스크립트 내 하드코딩
- 수집 키워드: `config.json`에서 관리

## 절대 하면 안 되는 것
- config.json 직접 수정 시 JSON 형식 깨지지 않도록 주의
- 수집 로직 변경 시 반드시 실제 테스트 후 배포

## 알려진 실패 케이스
- 네이버 구조 변경 시 파싱 실패 가능 → 셀렉터 업데이트 필요

## 검수 기준
- launchctl list | grep com.nomos.keyword-filter
- 로그 확인: logs/launchd_stdout.log
