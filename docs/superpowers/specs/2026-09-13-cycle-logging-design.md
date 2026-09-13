# 뉴스 회차 로그 통합 SPEC

## 목표
클라우드 콘솔에서 진행과 결과를 한눈에 확인한다. 기본 정상 로그는 회차당 종료 요약1줄이며 예외/느린 진행만 추가한다. 사용자 승인: root SPEC/검토, Luna 구현, 검증 후 commit/push. 서버 배포/PM2/실제 API 호출 없음.

## 변경 대상
gnews_tracker 운영 회차, news_pipeline 단계, llm_helper HTTP 시도, revalidate/push_notification의 반복 출력, 작은 공통 로그 모듈과 관련 테스트/README. dry-run 결과 JSON은 유지. legacy 서비스의 전송/수집 로직은 바꾸지 않는다.

## 동작/설계
- 표준 logging 기반 단일행 key=value 형식: UTC 시간, INFO/WARN/ERROR/DEBUG, cycle 식별자, event/stage/status. 기본 INFO, GNEWS_LOG_LEVEL=DEBUG 선택 가능. 전역 stdout redirect/print monkeypatch는 금지한다. 기존 output 주입 테스트 호환은 유지한다.
- 회차 컨텍스트에 통계/현재단계를 보관하고 helper들이 선택적으로 참여한다(작은 ContextVar 기반 모듈 가능). 컨텍스트 없는 공용 helper는 기본 logging으로 안전하게 출력한다. 로그 중복 handler 설치 금지.
- finally에서 정상/후보없음/선별없음/수집실패/DB실패/AI실패/예산차단 등 모든 종료에 cycle_summary 정확1줄. fetched,candidates,selected,saved,duplicates,rejected,cut,quality_failed,ai_calls,retries,db_failures,notify_failures,elapsed,stage/status, 가능한 budget_used/limit 포함. 모르는 값은 unknown 또는 생략하고0으로 꾸미지 않는다. ai_calls는 HTTP 직전 실제 시도만 집계, 예약거절은0.
- AI 파이프라인이 실패를 결과로 반환할 때에도 실패단계/원인이 컨텍스트로 전달되어 성공으로 기록되지 않는다. 재시도는 공유 추가시도가 실제 실행될 때 집계. 품질 미해결과 정상 선별탈락을 구분한다. 호출수/재시도/DB/알림 동작은 변경하지 않는다.
- 정상 요청 예산예약·전송 시작/성공·개별기사/구독자 상세는 DEBUG 또는 회차 합계로 대체. 동일 실패의 helper/상위 중복 출력은 제거. 재시도 발생은 WARN1줄, 최종실패는 요약에 반영하고 필요한 단계별 원인을 짧게 기록한다.
- 차단 최초 감지/해제 시 상태변경 로그. 반복차단 회차는 summary status=blocked만 출력. 가능한 차단종류와 blocked_until 표시. 차단상태 전환 추적은 메모리 TrackerState/로그상태로 충분하며 새로운 영속 시스템은 만들지 않는다.
- 진행이 오래 걸릴 때 회차당1회만 WARN event=cycle_slow stage=현재단계 elapsed=... 출력. 기본60초, GNEWS_SLOW_CYCLE_SECONDS 양의 수 설정. daemon Timer를 사용한다면 finally 취소하고 완료 후 출력되는 경합을 잠금/종료표시로 방지한다. 타이머는 요청/재시도/취소를 실행하지 않는다. 테스트는 실제60초 대기 없이 구동한다.
- revalidate/push 로그도 같은 회차 컨텍스트를 사용한다. 내부에서 실패를 삼키는 경우도 집계해 notify_failures에 드러나게 하되 반환/전송 계약은 보존. 배치/사용자별 반복실패는 합산하며 subscriber ID/FCM token은 기록하지 않는다.
- 예상치 못한 예외는 클래스/단계와 필요시 파일·함수·라인의 안전한 스택 위치만 기록한다. str(exception), 요청 URL/query/header/body, 응답원문, .env 값, 기사본문/전체 AI 응답, token/키는 모든 레벨에서 출력 금지. 소스에 존재하는 하드코딩 수동 테스트 토큰은 제거하고 환경변수로 받도록 하되 값은 출력하지 않는다.

## 제약/예외
무료 모델/5분 스케줄/단계별 토큰/회차3 HTTP/990 일일예산/저장·발행 계약 유지. 새 패키지/DB 스키마/로그 서버 추가 없음. 로그 보관/rotation은 서버 운영 설정이므로 README에 별도 작업이라고 설명하고 실제 설정은 변경하지 않는다. 임포트 시 외부 Firebase/DB 호출이 있는 파일은 테스트에서 의존성을 mock한다.

## 완료 조건
- 기존 전체tests 통과, 새 테스트: 모든 조기반환도 summary1줄; 정상2HTTP/최악3HTTP count; 예약차단0HTTP; 재시도/품질실패/DB실패/내부알림실패 식별; 연속429 상세중복 억제/해제; 느린단계1회/종료후미출력; DEBUG 제어; 가짜 secret을 예외/응답에 넣어도 출력안됨.
- helper 정상출력 감소 확인, 기본 정상1회차 summary1줄. 설정과 로그예시 README 최신화.
- root 리뷰, 문법/전체 오프라인 테스트/diff/비밀 값 검사 후 명시파일 커밋/일반push. 실제 외부 요청/배포 없음.

## 최종 검증 기록
- Luna 구현 후 root 독립 검증 및 리뷰 수정 완료. 전체 오프라인 테스트217개, 변경 Python 문법, diff/비밀 값 검사 통과.
- 실제 HTTP 함수 모킹: 정상 회차1줄/2 HTTP, 회복 재시도2줄/3 HTTP, 기존 영속 차단 중0 HTTP와 최초 상세1회, 다음날 해제 상세1회/2 HTTP/재시도0/예산2 확인. 주입 출력과 기본 로거의 중복 없음.
- 내부 알림 실패2건+별도 publisher 예외1건=3건 집계, 가짜 민감 예외 문자열 미출력 확인.
- FCM UNREGISTERED 실패1건 집계와 기존 만료 구독 삭제 확인 및 영속 회귀 테스트 추가.
- 서버 로그 회전/보관 설정, 배포, PM2 재시작은 수행하지 않는다. 사용자 승인 범위의 소스 커밋/일반 push만 이어서 수행한다.
