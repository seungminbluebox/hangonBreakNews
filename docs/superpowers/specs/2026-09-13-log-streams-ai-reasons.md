# 로그 출력 분리와 AI 실패 원인 SPEC

## 목표/범위
사용자가 관찰한 PM2 INFO의 error.log 유입을 수정하고, invalid_response에 가려진 실패 원인을 표시한다. Luna 구현, root 검토/검증/commit/push. 서버 배포·PM2 재시작·실제 API 호출 금지.

## 설계
- cycle_logging의 소유 handler를 두 개로 구성: DEBUG/INFO/WARN(<ERROR)은 stdout, ERROR/CRITICAL은 stderr. 수준 필터가 상호배타적이어야 하며 중복 handler/전파 없음. GNEWS_LOG_LEVEL 및 주입 output 단일 sink/slow warning 동작 유지. 정상 형식/회차당 summary1줄 유지. 실패 summary는 INFO 그대로이며 실제 실패 event만 ERROR.
- 고정 원인 코드를 사용: timeout, network_error, http_error, empty_response, invalid_json, output_truncated, schema_error, unexpected_error. 예산/429 차단 경로는 기존 유지. exception 문자열 매칭 대신 명시적인 오류 타입/안전 메타로 분류한다.
- helper가 실패 후 None을 반환하여 원인이 소실되는 문제는 GNews 전용 opt-in raise_on_failure 인자로 해결한다. 기본 False로 기존 호출자는 None 반환 계약 유지. True이면 마지막 실패의 고정 reason과 선택적 숫자 status_code만 가진 안전한 오류를 던진다. GNewsStageGenerator가 True를 전달한다. pipeline은 이 reason을 allowlist로 받아 재시도 WARN와 최종 ERROR/summary failure_reason에 동일하게 기록한다. 원문/응답본문/키/URL/예외메시지를 기록하지 않는다.
- pipeline의 빈 text, JSON 파싱, 배열이 아닌 JSON/필드·ID 불일치, finish_reason=length를 각각 구별한다. length는 빈 content보다 우선한다. SDK 응답 envelope 비정상도 schema_error, HTTP 응답 JSON 파싱 실패는 invalid_json. requests Timeout은 network_error보다 먼저 분류한다. 예외를 삼키거나 새 API 호출을 추가하지 않는다.
- 기존 공유 재시도1회, 회차최대3HTTP, 일일990, 무료 모델만 사용, 수집/저장/발행과 토큰 설정 유지. valid summary [] 같은 의미상 누락 처리나 timeout 정책 변경은 이번 범위 밖이며 후속 우선순위로 설명한다.

## 완료 조건
- 실제 stdout/stderr를 캡처해 INFO/WARN은 stdout만, ERROR는 stderr만, DEBUG level 및 반복초기화/주입sink 중복없음을 검증한다.
- 실제 requests.post mock 기반 Timeout/connection/HTTP503/빈응답/JSON오류/잘림/형식오류의 retry 및 final reason 확인. 두단계HTTP최대3 보존, 기존 helper None 계약 보존. synthetic secret을 예외/응답에 넣어도 stdout/stderr/sink에 출력되지 않는다.
- 전체 tests, 문법/diff/비밀 검사, README 원인코드·PM2 출력 위치 설명 갱신 후 commit/push. 기존 서버 error.log 내용은 자동 이동/삭제되지 않으며 새 프로세스 적용 이후 출력부터 바뀐다.

## 검증 결과
- Luna 구현, root 리뷰 및 독립 검증 완료. 전체 오프라인 테스트221개, 문법/비밀 값 검사 통과.
- root 실제 requests.post 모의 검증10개: HTTP/응답의8개 원인과 content JSON/형식 오류. 재시도 WARN·최종 ERROR·summary의 원인이 일치하고, INFO/WARN stdout·ERROR stderr 배타 출력 및 민감 원문 미출력 확인.
- 선별 성공 후 요약 Timeout2회 상황에서 실제HTTP3회/공유재시도1회 유지. ID 불일치 오류도 ERROR1회와 실패 summary를 남기는 회귀 테스트 통과.
- 서버 배포/PM2/실제 서비스 API 호출 없음. 검증된 소스만 사용자 요청 범위에서 commit/push한다.

## 후속 개선 후보 (이번 구현 제외)
1. 요약 누락 감지: 선별1개 후 요약[]일 때 saved=0/quality_failed=0/status=ok가 되는 현상을 모의 재현했다. 선별 수·요약 수·누락 수를 분리하고 누락을 partial로 표시하는 개선이 우선이다.
2. 회차 전체 시간 예산: 사용자 로그의171~253초 지연을 줄이기 위한 단계별 시간 계측과 총시간 제한을 검토한다. requests timeout은 응답 전체 다운로드 시간 상한이 아니다. [공식 설명](https://requests.readthedocs.io/en/latest/user/quickstart/#timeouts)
3. 무료 모델별 품질·지연 계측: 실제 라우팅된 무료 모델과 단계별 소요시간/실패율을 비교한 뒤 입력량·프롬프트를 조정한다. 유료 모델 도입은 제안하지 않는다.
