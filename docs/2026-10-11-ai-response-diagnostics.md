# AI 응답 실패 진단 — 검토용 패치

## 확인한 경로와 한계

기준은 수집기 원본 main `1793945a9a8e4f19c31b0655dfb7309ce756023c`다.
2026-10-11 UTC10:43:48 읽기 전용 확인에서 운영 GNews의 공개 모델명/토큰
override 4개는 프로세스 환경과 dotenv에 없었다. 설치된 소스 해시는 기준과
일치한다. 따라서 GNews builder는 두 모델 모두 `openrouter/free`, 두 단계
각각 `max_tokens=8192`, 요청 timeout60초를 사용한다. 실제 HTTP 요청 횟수는
정상2회 및 회차 공통 재시도1회, 일일 제한990회다. 이 설정은 변경하지 않는다.

`llm_helper.py`는 HTTP 응답의 첫 choice가 `finish_reason="length"`이면
`output_truncated`를 발생시킨다. `_response_text`도 동일 조건을 거부한다.
`schema_error`는 제공자 envelope의 choices/message/content 형태 오류,
pipeline의 배열/선별/제목 필드 계약 오류, 최종 ID 연결 오류를 함께 가리켰다.
기존 INFO 로그에는 실제 선택 모델, content 문자수, completion/reasoning token
집계와 이 경계 구분이 없었다. 기존 실패의 세부 원인을 사후 복원할 수 없다.

OpenRouter 공식 문서는 reasoning 토큰이 대체로 max_tokens를 공유하며,
reasoning이 한도를 소진하면 content 없이 finish_reason=length가 나올 수
있다고 설명한다. 이는 가능한 원인이고 이번 운영 실패의 확인된 원인은 아니다.
`openrouter/free`는 고정 모델이 아니므로 요청 모델명만으로 실제 모델을 단정하지
않는다. strict schema의 실제 준수도 endpoint에 따라 다르다.

- https://openrouter.ai/docs/guides/best-practices/reasoning-tokens
- https://openrouter.ai/docs/guides/features/structured-outputs
- https://openrouter.ai/docs/api_reference/overview

## 최소 변경

GNews가 사용하는 `raise_on_failure=True` 경로에만 INFO `ai_response_metadata`를
추가한다. 공개 형식의 요청/응답 모델 ID, 고정 finish reason, 응답 문자수,
설정 max_tokens와 정상 범위의 정수 completion/reasoning token 집계만 남긴다.
provider body, prompt, content, reasoning 텍스트, 헤더, 키, response ID, 비용,
전체 usage 객체를 기록하지 않는다. 비정상 모델/종료 문자열은 unknown으로
대체하며 boolean/문자열/음수 등 비정상 token 집계는 생략한다.

transport schema 오류는 고정 detail(invalid_envelope/invalid_choices/
invalid_message/non_text_content)을 기록한다. pipeline 계약 오류는 고정
detail(non_array/selection_fields/headline_fields/identity)로 분리한다.
오류·선별·저장 결과, 품질 검사, 예산, 모델, 토큰, timeout, 공통 재시도 횟수와
기존 legacy 기본 호출의 로깅 계약은 유지한다. 잘린 응답을 발행하거나 계약을
완화하지 않는다. JSON 복구 호출이나 추가 외부 모델 요청을 만들지 않는다.

## 오프라인 검증과 운영 경계

실제 dotenv 로딩을 차단하고 synthetic HTTP 응답/임시 SQLite 예산으로 검증한다.
기존 코드에서 새 진단 테스트5건의 실패를 확인한 뒤 패치를 준비했다.
전체 tests/ 289건 통과. 토큰 소진형 잘림, envelope 오류, 계약 오류, 공개
메타데이터 sanitization, 비정상 집계 생략과 legacy opt-out을 검증한다.
초기 실행의 cwd 오류3건은 원본 cwd/-X utf8 재실행으로 해소됐다.

독립 리뷰의 방어적 projection 보완으로 max_tokens도 동일한 숫자 검증을 거친다.
string/boolean/음수/과대값 4개 subcase의 RED를 확인하고 수정 후 전체289건 PASS.
기존 helper 진입부는 positive int를 먼저 검증하므로 이 보완을 운영 비밀 노출의
발견으로 해석하지 않는다.

이 문서는 검토 시점의 미커밋 패치를 기록한다. 검토 중에는 운영에 적용하거나
예산을 상향하지 않았다. 이후 승인된 게시·운영 적용은 별도 실행 기록으로
검증한다. synthetic PASS를 실제 서비스 회복으로 주장하지 않는다. 자연 저장
성공은 새 DB 행과 saved 로그로 별도 대조해야 한다. 본문 column DROP과
Pulse 생성은 보류한다.
