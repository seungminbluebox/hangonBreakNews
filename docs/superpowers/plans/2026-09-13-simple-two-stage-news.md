# Simple Two-stage News Implementation Plan

**Goal:** 무료 뉴스 선별/요약 분리와 회차당 실제 HTTP최대3회, 최신 수집만 처리.
**Architecture:** 수집 → 규칙필터 → 선별1회 → top10 → 요약1회 → 기존 검증/DB. 두 AI 단계 공통 추가시도1회. 영속 기사 대기열 없음.
**Tech Stack:** 기존 Python/requests/unittest/SQLite.
**Spec:** ../specs/2026-09-13-simple-two-stage-news-design.md

프로젝트 AGENTS의 간결한 문서 우선 원칙을 적용한다. IMPLEMENT는 사용자 지정Luna, root는 문서/검토/커밋/푸시를 맡는다.

- [x] 새 pipeline의 공개진입점과 response schema, 선별/요약 분리·top10·공통 retry를 실패하는 오프라인 테스트로 고정한다. 기존 품질규칙 테스트는 보존한다.
- [x] llm_helper에 단계별 max_tokens/response_format와 single-attempt 경로를 적용하고, GNews generator에서 free-only 설정을 검증한다. 운영/preview 모두 새pipeline을 사용한다.
- [x] worker를 현재회차100후보 단일 selector 호출로 단순화하고 결과의 rejected/failed/omitted URL을 구별해 평가이력/DB쓰기를 처리한다. 누적 pending/품질보정 루프는 운영에서 제거한다.
- [x] 실제post spy로 정상/최악/429/288회차864을 검증한다. 기본990 영속budget 테스트와 기존 tests/를 실행한다. 예외/식별자/실패 후재수집/토큰옵션검증을 포함한다.
- [x] README/조사보고서 업데이트, 문법/diff/비밀 검사, root 최종검토 완료. 사용자 승인에 따라 명시파일 커밋 및 일반push를 이어서 수행하며 결과는 최종 보고에 기록한다. 서버접속/배포/PM2는하지않는다.
