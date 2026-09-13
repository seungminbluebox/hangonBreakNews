# GNews 경제 검색 보강

## 목표
기존 business/world 수집을 유지하며 검색으로 경제·거시경제 후보를 보강한다. 사용자 승인 범위는 수집 계층이며 AI 프롬프트, 요약, DB, 알림 정책은 변경하지 않는다. SPEC/검증은 root, IMPLEMENT는 사용자가 지정한 Luna가 담당한다.

## 변경 대상
gnews_adapter.py, 필요한 최소 cycle_logging.py 요약 필드와 gnews_tracker.py 부분 수집 상태 분류, 관련 오프라인 테스트, readme.

## 동작/설계
- 300초 주기와 기존 짝수 사이클 business 3개 + world 1개를 유지한다.
- 홀수 사이클은 world + search 1개. search는 ko_macro, en_macro, ko_policy, en_policy 순환. 각 묶음은 40분마다 조회하며 프로세스 재시작 시 처음부터 시작한다.
- /api/v4/search: lang=ko/en, 국가 제한 없음, in=title,description, sortby=publishedAt, 최근 3시간 from/to, max=25. 기존 명시적 max_articles override는 일관되게 적용한다. 추가 페이지 조회 없음.
- 다음 검색식을 각각 q로 사용한다(각각 200자 이하).

ko_macro:
기준금리 OR 통화정책 OR 한국은행 OR 연준 OR 소비자물가 OR 인플레이션 OR 실업률 OR 비농업고용 OR 국내총생산 OR 경제성장률 OR 경상수지 OR 무역수지 OR 국가부채 OR (환율 AND (급등 OR 급락 OR 개입)) OR (국채 AND (금리 OR 발행))

en_macro:
"interest rate" OR "central bank" OR inflation OR CPI OR payrolls OR unemployment OR GDP OR recession OR "bond yields" OR "sovereign debt" OR "currency intervention" OR "trade balance"

ko_policy:
(무역 OR 관세 OR 제재 OR 공급망 OR 에너지 OR 원유 OR 가스 OR 반도체 OR 인프라) AND (협상 OR 협력 OR 합의 OR 투자 OR 규제 OR 수출 OR 수입 OR 감산 OR 증산 OR 중단 OR 봉쇄 OR 지원 OR 요청 OR 논의 OR 재확인)

en_policy:
(trade OR tariffs OR sanctions OR energy OR oil OR semiconductor OR infrastructure) AND (talks OR cooperation OR agreement OR investment OR restrictions OR exports OR disruption OR supply)

- 검색 기사는 기존 normalize_article 형태로 반환하며 market_scope는 ko=kr, en=world. 기존 ID/URL 중복 제거와 후속 AI 후보 처리를 그대로 사용한다.
- 검색도 기존 before_request 예산 차감 및 최대 2회 재시도(총 3 HTTP 시도)를 공유한다. 새 별도 예산/재시도 계층을 만들지 않는다.
- 검색 실패 시 이미 수집한 world 기사를 버리지 않는다. 검색 오류는 안전한 종류만 기록하고 fetch_failures에 반영한다. 키/요청 URL/응답 본문/원문 예외 문자열은 출력 금지. 기존 headline 실패 정책은 유지한다.
- 일부 수집 성공 후 검색 실패는 cycle_summary status=partial로 표시한다(더 심각한 AI/DB/알림 실패·차단 상태가 우선). 아무 기사도 수집하지 못한 수집 실패는 fetch_failed를 유지한다.
- 검색 실행 사이클의 기존 cycle_summary에 search_group, search_fetched(정규화/시간 필터 후 중복 제거 전 건수)를 추가한다. 별도 성공 INFO 줄은 추가하지 않는다. 문맥 없는 collector 사용도 지원한다.

## 제약/예외
- 288 사이클/일 정상 운전 시 headline 720 + search 144 = 864 HTTP 요청. 각 검색 묶음 36회/일. 재시도 추가분은 기존 GNews 950 안전 제한에 포함된다. 재시작/다른 프로세스는 기본 계산의 전제 밖이다.
- 기본 원시 후보 최대는 짝수 100, 홀수 50. AI 한도/무료 모델 전용/990 예산/사이클 최대 3회는 그대로다.
- 모든 기사의 발견·저장이나 중요도 향상을 보장하지 않는다. 검색 결과 최대 25개와 순환 간격 때문에 누락 가능.
- 실제 API/DB/FCM 호출 없이 검증한다. 비밀 값 출력/커밋 금지. 로컬 수정과 git push까지만 수행, 서버 배포/PM2 재시작 금지.

## 완료 조건
- 테스트를 먼저 작성해 새 동작의 실패를 확인하고 최소 구현한다.
- mock HTTP로 endpoint/검색식/언어/날짜/25개/국가 미지정, 정규화, 중복, 재시도별 예산 차감, 검색 실패 시 world 보존을 검증한다.
- 288 사이클 시뮬레이션으로 864 요청, 검색별 36회 및 기존 피드 횟수를 검증한다.
- 검색 요약 로그와 민감정보 비노출, 문법 검사, 전체 tests/ 회귀 테스트를 통과한다. root test.py는 실행하지 않는다.
- 사용 설명을 최신화하고 검토 후 커밋/푸시한다.

검증 결과(2026-09-13): 전체 오프라인 테스트 229개, 변경 Python 문법 검사, git diff --check, 변경 파일 비밀정보 검사 통과. 별도 읽기 전용 코드 리뷰에서 P1/P2 회귀 없음. 실제 GNewsClient에 가짜 HTTP opener를 연결한 288사이클 검증에서 총 864회(world 288, business 각 144, search 각 36)를 확인했다. 실제 API/DB/FCM 및 서버 배포는 수행하지 않았다.
