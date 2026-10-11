# 속보 제목 전용 전환

## 동작과 호환 계약

운영 진입점은 `gnews_tracker.py`, RSS 롤백 진입점은 `breaking_tracker.py`다.
기존 서버 문서는 `~/hangon_breaknews`와 PM2 `breaking-news`를 기록하지만 OCI의
실제 경로·프로세스 이름은 확인되지 않았다. 이를 OCI 경로로 추측하지 않는다.
현재 NAVER 운영은 정상이며 OCI 전환은 준비 후 별도 운영 작업이다. 이번 작업에서
서버에 접근하거나 수집·배포·재시작하지 않았다.

- GNews는 기존 후보 선별 후 상위 10건의 제목만 생성한다. 제목과 원문 근거만 반환하며
  본문요약은 생성하지 않는다. 원문은 제목의 사실 검증에 사용할 수 있다.
- 내부 `source_excerpt`는 12~600자 실제 연속 원문인지 검증하며 DB, 푸시, 미리보기에
  공개하지 않는다. 원문의 국가·기업·직책·일부 다중 화자 검증을 유지한다.
- 피해인구·사망자·부상자·실종자·이재민의 명시적 수치를 구분한다. 수치의 역할,
  보고기간, 방향, 확률과 확인 수준을 보존하며 제목은 잘리지 않은 55자 이내다.
- 실패 시 기존 한국어 원제목만 같은 검증을 거쳐 사용할 수 있다. 설명이나 본문을
  발췌·인용해 공개하지 않는다. 안전한 제목이 없으면 실패 사유를 기록하고 재수집한다.
- 기본 `BREAKING_NEWS_CONTENT_COLUMN_MODE=legacy`에서는 새 행의 `content`가
  빈 문자열이다. 확인된 운영 메타데이터는
  `public.breaking_news.content text NOT NULL`이며 기본값이 없다. 이 호환 쓰기는
  NULL 제약을 충족하면서 요약을 남기지 않는다. 기존 행은 변경하지 않는다.
- `source_content text NULL`은 Pulse 원문용 비공개 원문이다. GNews `raw_content`를
  그대로 보존하는 기존 계약을 유지한다. 삭제 대상 요약 `content`와 구분한다.
- 사용자는 `content`의 완전 삭제를 요청했으며 백업·복구·아카이브는 필요 없다고
  지정했다. 삭제 시 기존 요약은 다른 열에 복사하거나 보관하지 않는다.
  `source_content`는 제목 검증·Pulse에 필요하므로 유지한다. 실제 DROP은 아직 미실행이다.
- GNews의 기존 24시간 중복 조회는 `title,source_content,created_at`만 읽는다.
  RSS 롤백도 `title,source_content`만 읽는다. 내부 비교 객체의 `content` 키는
  비공개 원문 근거를 담으며 DB 요약 `content`를 읽거나 원문으로 위장하지 않는다.
  원문 없는 행은 제목만 비교한다. 동일 배치 내 저장 후 근거도 원문만 사용한다.
  `original_url`, 중요도·카테고리, 선별, 재시도 예산과 알림 대상 기준은 유지한다.
- 푸시 제목과 링크·구독자 선별은 유지하며 알림 body는 빈 문자열이다. 미리보기는
  제목과 공개 메타데이터만 출력한다. 원문과 내부 근거는 출력하지 않는다.
- GNews의 옛 `run_cycle`/`run_dry_run` 기본 selector도 제목 전용 adapter를 사용한다.
  `select_and_summarize`도 기본값은 제목 전용이다. 옛 단일단계 요약 구현은
  `headline_only=False`를 명시한 역사적 검증 호환으로만 보존한다. 운영 및
  롤백에서 사용하지 않으며 요약을 만들도록 요청하지 않은 호출도 생성하지 않는다.
- `GNEWS_SUMMARY_MAX_TOKENS`는 기존 운영 설정과 호환되는 제목 단계 토큰 한도
  이름으로 계속 지원한다. 키 값을 읽거나 환경 파일을 수정할 필요가 없다.

## 배포 순서와 승인 경계

1. 프론트엔드 제목 전용 화면·공유 출력과 백엔드/Pulse의 `source_content` 전용
   상세 근거 읽기를 먼저 게시한다. 원문 없는 옛 행은 상세 근거를 제공하지 않는다.
   기존 생성 요약을 원문으로 간주하지 않는다. 행의 요약은 아직 변경하지 않는다.
2. 수집기는 기본 `legacy`로 게시한다. 두 운영 경로는 `content=''`를 명시하고
   현재 NOT NULL/기본값 없음 스키마와 호환된다. 코드 게시만으로 DB를 변경하지 않는다.
3. 삭제 전에 omit 저장을 검증해야 할 때만 DB 소유 저장소의 선택적 expand SQL로
   `content DEFAULT ''`를 준비한다. 기존 값·원문을 변경하지 않고 보존 조건도 없다.
   SQL 실제 실행 지시 전에는 적용하지 않는다.
4. 선택한 사전 배포 순서에 따라 기본값 호환을 확인한 후에만 승인받아
   `BREAKING_NEWS_CONTENT_COLUMN_MODE=omit`으로 운영 GNews/RSS를 배포한다.
   omit은 INSERT에서 `content` 키 자체를 제외한다. expand 후 DROP 전과 DROP 후를
   같은 계약으로 처리하며 NULL을 명시하지 않는다. 현재 스키마에서 먼저 전환하면
   INSERT가 거부된다. 오류 시 자동 legacy fallback이나 스키마 조회·변경을 하지 않는다.
   유효값은 정확히 `legacy|omit`이며 잘못된 값은 시작/저장 계약 검증에서 차단한다.
5. 운영 pull, 실제 dry-run(외부 API 호출), PM2 재시작, 플래그 변경은 별도 운영
   승인 후 수행한다. 이 코드 작업에서 서버에 접근하거나 재시작하지 않는다.
6. 실제 새 행 저장·원문 없는 Pulse 동작·공개 제목 전용 출력·원문 비공개 권한을
   검증하고 모든 옛 writer/rollback의 content 쓰기를 종료한다. 최신 DB/외부 의존성을
   확인하고 실제 실행 지시 후 최소 contract SQL의 `DROP COLUMN content RESTRICT`를
   적용한다. DROP 후에는 `legacy`나 content를 쓰는 구버전을 실행하지 않는다.
   복구 SQL은 준비하지 않는다. 수동 SQL의 기준 문서는 백엔드
   `docs/product/breaking-news-title-only.md`다. 실제 DROP·운영 변경은 미실행이다.

## OCI 준비 후 최소 운영 절차

OCI 준비·운영 승인 후 실제 checkout 경로·git remote/main SHA, GNews/RSS 진입점,
실행 관리자/프로세스 이름과 비민감 mode 설정을 먼저 확인한다. 고정 경로나 재시작
명령을 추측하지 않고 키 값을 출력하지 않는다. 확인한 환경에 최신 main과 omit
모드를 반영하고 승인된 대상 프로세스만 시작/재시작한다. NAVER/OCI 중복 수집을
피하도록 실행 위치를 조율한다. 이 단계들은 이번 문서 작업에서 수행하지 않는다.

승인된 실제 수집 뒤 새 행의 제목·원문 보존, URL/사건 중복 방지, 빈 push body,
제목 전용 공개 화면과 source_content 비공개 권한을 확인한다. DROP 전에는 omit
저장이 정상이어야 하고, DROP 후에는 content 열 없음·source_content 유지·omit
저장·중복 조회·원문 없는 Pulse 근거 제외를 확인한다. SQL 실행과 코드 게시·운영
배포 결과는 각각 보고한다.

## 검증 범위와 한계

회귀검증은 합성 및 기존 관찰 fixture와 mock HTTP/DB/알림으로 수행한다. 실제 모델
출력 품질, 실제 FCM 빈 body의 기기 표시, 서버 실행 파일/배포 상태, 사용자 화면은
오프라인 테스트만으로 검증되지 않는다. 명시적 수치 역할과 알려진 이름에 대한
검증은 일반적인 의미 추론 모델이 아니며 원문 전체의 정확성을 보장하지 않는다.
