# 📡 Hang on! 멀티 트래커 운영 가이드

이 가이드는 **경제 속보 트래커**와 **환율 급변 모니터링** 시스템의 통합 운영을 위해 작성되었습니다.

## 1. 서버 접속 및 환경 진입

서버에 접속한 후 파이썬 가상 환경을 활성화하는 기본 단계입니다-

```bash
# 1. 접속
https://console.cloud.google.com/

# 2. 프로젝트 폴더 이동
cd ~/hangon_breaknews
cd ~/kospinight

# 3. 가상 환경 활성화
source venv/bin/activate
# 4. 비활성화
deactivate

```

---

## 2. 코드 및 라이브러리 업데이트

로컬에서 수정한 코드를 서버에 반영하고, 새로운 라이브러리를 설치하는 과정입니다-

```bash
# 1. 최신 코드 가져오기 (GitHub)
git pull origin main

# 2. 새로운 라이브러리 설치 (필요 시)
# 환율 트래커를 위해 finance-datareader, pandas가 포함되어야 합니다.
pip install -r requirements.txt

# 3. DB에 쓰지 않는 1회 점검
python gnews_tracker.py --dry-run

# 4. 기존 RSS 트래커를 중지하고 GNews 트래커로 교체
pm2 stop breaking-news
pm2 delete breaking-news
pm2 start gnews_tracker.py --name breaking-news --interpreter ./venv/bin/python
pm2 save

# 다른 트래커 재시작
pm2 restart exchange-monitor
pm2 restart kospi-night

```

---

## 3. 모니터링 및 로그 확인

트래커들이 실시간으로 데이터를 잘 낚아오고 있는지 확인하는 방법입니다-

- **실시간 로그 확인 (하나씩 보기):**

```bash
pm2 logs breaking-news    # 뉴스 트래커 로그
pm2 logs exchange-monitor # 환율 트래커 로그
pm2 logs kospi-night # 코야선 로그

```

- **통합 로그 확인 (모든 프로세스):**

```bash
pm2 logs

```

_(나가려면 `Ctrl + C`를 누르세요-)_

- **프로세스 상태 요약:**

```bash
pm2 status

```

---

## 4. 시스템 자원 모니터링 (RAM & Disk)

GCP 인스턴스의 자원 상태를 주기적으로 확인하여 서버 멈춤을 방지하세요-

- **RAM 및 Swap 사용량 확인:**

```bash
free -h

```

- **스왑(Swap) 설정 상태 상세 확인:**

```bash
sudo swapon --show

```

- **디스크(Disk) 남은 용량 확인:**

```bash
df -h

```

---

## 5. 주요 관리 명령어 요약

| 명령어                 | 설명                                   |
| ---------------------- | -------------------------------------- |
| `pm2 status`           | 모든 트래커 작동 상태 확인             |
| `pm2 logs --lines 100` | 최근 로그 100줄씩 몰아보기             |
| `pm2 restart all`      | 모든 서비스 한 번에 재시작             |
| `pm2 save`             | 현재 실행 상태 저장 (재부팅 대비 필수) |
| `grep -E '^[A-Z0-9_]+=' .env \| cut -d= -f1` | 값 노출 없이 환경 변수 이름만 확인 |

---

## 6. 주의 사항

- **프로세스 명칭**: 기존 `tracker`는 `breaking-news`로 이름이 변경되었습니다-
- **실행 파일**: `gnews_tracker.py`가 운영 파일이며 기본 5분 주기로 실행됩니다. `breaking_tracker.py`는 롤백용으로만 남겨 둡니다.
- **DB compatibility**: `BREAKING_NEWS_CONTENT_COLUMN_MODE=legacy`(기본)는 제목만 생성하고 `content=''`를 명시해 현재 NOT NULL/기본값 없음 제약을 만족합니다. 승인된 expand/default 적용 후에만 `omit`으로 전환하면 INSERT에서 `content` 필드 자체를 생략합니다. 잘못된 모드는 차단하며 DB 오류 기반 자동 모드 전환·스키마 변경은 없습니다. 운영 GNews와 RSS 롤백 모두 같은 계약을 사용합니다. 플래그 변경·서버 재시작·실제 DROP은 별도 승인 작업입니다.
- **Source-content deployment**: Apply the privacy prerequisite and migration in [docs/breaking-news-source-content-deployment.md](docs/breaking-news-source-content-deployment.md) before restarting `gnews_tracker.py`.
- **카테고리**: `market`, `indicator`, `geopolitics`, `corporate`에 `policy`가 추가됐습니다. `policy`는 법률·세제·정부 정책과 시장·산업·다수 기업 또는 소비자에게 적용되는 규제이며, 특정 기업만 대상으로 한 규제 집행은 `corporate`입니다.
- **배포 전 DB 확인**: 이 저장소에는 `category` CHECK 제약 마이그레이션이 없습니다. 운영 DB에서 아래 쿼리로 제약을 확인하고, 허용값이 고정돼 있으면 `policy`를 추가하는 별도 검토된 마이그레이션을 먼저 적용하세요.

```sql
select conname, pg_get_constraintdef(oid)
from pg_constraint
where conrelid = 'public.breaking_news'::regclass
  and contype = 'c';
```

- **배포 순서**: 프론트엔드의 `정책/규제` 필터 지원을 먼저 배포하거나 백엔드와 동시에 배포한 뒤 `gnews_tracker.py`를 재시작합니다.
- **수집 시간창**: GNews 요청과 응답 검증 모두 최근 3시간 이내 작성 기사만 허용합니다.
- **사건 시간창**: 기사에 명시된 사건일이 작성일보다 3일 넘게 오래됐으면 제외합니다. 단, 오늘 시행·신규 집행·새 수치 같은 후속 사실과 미래 시행일은 유지합니다.
- **회차별 후보량**: 짝수 회차는 한국·미국·세계 business와 world headline을 각 최대 25건(원시 최대 100건) 가져오고, 홀수 회차는 world headline과 순환 검색식 1개를 각 최대 25건(원시 최대 50건) 가져옵니다. 검색은 10분마다 `ko_macro`, `en_macro`, `ko_policy`, `en_policy` 순서로 바뀌고 같은 검색식을 40분마다 다시 조회합니다. 5분 간격 288회차 기준 하루 기본 요청은 재시도 제외 864회(헤드라인 720 + 검색 144)입니다.
- **중복 기준**: 동일 URL과 최근 24시간 내 동일 사건을 차단하며, 승인·완료·취소·공식 수치 수정처럼 상태가 달라진 후속 보도는 허용합니다. 동일 사건 뒤의 단순 시세 변화는 새 수치로 보지 않습니다. 같은 시장 하락의 지수·종목·산업 기사, 같은 분기 실적의 배당·순이익 기사, 같은 산업 정책의 세부 기사도 하나의 사건으로 묶습니다. 고용지표·실업률·일자리처럼 표현이 달라도 국가·발표 기간·핵심 수치가 모두 일치할 때만 같은 발표로 묶으며, 하나라도 다르면 별개 소식으로 보존합니다. AI에는 최신 100건만 비교 문맥으로 전달하고 최대 300건은 저장 전 규칙 기반으로 다시 검사합니다.
- **품질 기준**: 기존 선별 기준을 유지하며 제목의 수치 역할(피해인구·사망자 등), 국가·기업·대상, 보고기간·방향·증거 수준을 보존합니다. 완결된 55자 이내 한국어 제목만 생성하고 본문요약은 생성하지 않습니다. 핵심 의미가 담기지 않거나 검증이 실패하면 제외합니다.
- **원문 근거·제목 검수**: 제목 AI에는 선별 AI의 이유 대신 원문 제목·설명·본문과 코드가 확인한 이름을 전달합니다. `source_excerpt`가 실제 연속 원문인지 검사하고 이 내부 근거는 DB·알림·미리보기에 공개하지 않습니다. 국가·기업 이름과 직책, 투자 권유 및 일부 다중 화자 검증을 유지합니다. 근거 문자열 일치는 의미 전체의 정확성을 보장하지 않습니다.
- **안전한 원문 대체**: 제목 실패·누락 시 완결된 한국어 원제목만 같은 검증을 거쳐 보존할 수 있습니다. 원문 본문·설명은 공개하거나 인용하지 않습니다. 안전한 제목이 없으면 `news_quality_failed` 사유를 기록하고 재수집 대상에 남깁니다. 대체 건수는 기존 `source_fallbacks` 로그에 기록합니다.
- **2026-10-08 품질 검증 기록**: 실제 관찰 문장, 공개 원문 확인 범위, 오프라인 회귀 검증과 한계는 [품질 검증 보고서](docs/2026-10-08-live-news-grounding.md)에 정리했습니다. 기존 저장 행 수정이나 서버 배포·재시작은 포함하지 않습니다.
- **AI 처리 단계**: 운영과 dry-run은 최대 100개 후보를 한 번 선별한 뒤 상위 10개 제목만 한 번 생성합니다. 두 단계 공통 재시도 1회와 기존 다음 회차 재평가 정책을 유지합니다. RSS 롤백 경로도 본문요약을 생성하지 않고 같은 제목 품질 검사와 빈 content 저장·빈 push body를 적용합니다.
- **회차 로그**: 운영은 `cycle_summary` 한 줄에 `fetched`, `candidates`, `selected`, `saved`, `ai_calls`, `retries`, DB·알림 실패 수와 경과 시간을 기록합니다. 검색 회차에는 `search_group`과 정규화·시간 필터 후 `search_fetched`도 기록합니다. 기본 레벨은 INFO이며 `GNEWS_LOG_LEVEL=DEBUG`에서 단계 상세를 확인하고, `GNEWS_SLOW_CYCLE_SECONDS`(기본 60초)로 느린 회차 경고 기준을 조정합니다. INFO/WARN은 PM2 표준 출력으로, ERROR/CRITICAL은 표준 오류로 분리됩니다. 예시는 `ts=... level=INFO event=cycle_summary cycle=... stage=notify status=ok fetched=... ai_calls=2 retries=0 elapsed=...` 형식입니다.
- **AI 실패 원인**: GNews 단계 실패는 `timeout`, `network_error`, `http_error`, `empty_response`, `invalid_json`, `output_truncated`, `schema_error`, `unexpected_error` 중 하나로 `reason`과 `failure_reason`에 기록합니다. 예외 원문·응답 본문·URL·토큰은 로그에 기록하지 않습니다.
- **GNews 요청 예산**: 기존 950회 안전 제한을 검색과 헤드라인이 공유하며 재시도도 차감합니다. 요청량은 프로세스 메모리에 기록되므로 재시작하면 초기화되고, 다른 프로세스나 공유 키의 호출량은 합산하지 않습니다. 864회는 정상 수집 기준이며 장애 재시도가 늘면 950회 제한에 먼저 도달할 수 있습니다.
- **GNews 검색**: 검색은 제목·설명에서 최근 3시간의 경제·정책 기사를 조회하며 국가 제한 없이 `publishedAt` 내림차순으로 최대 25건을 요청합니다. 검색 실패 시 같은 회차에서 이미 수집한 world 기사는 유지하고, 오류 종류만 회차 실패 통계에 기록합니다.
- **로그 보관**: 이번 변경은 애플리케이션 출력 통합입니다. 서버 로그 회전·파일 크기·보관 기간 설정은 별도 운영 작업이며 변경하지 않았습니다.
- **속보 기준**: 기사 종류와 관계없이 영향 범위·변화 규모·시장 즉시성 중 두 가지 이상을 강하게 충족하면 9점 속보, 세 가지 모두 충족하며 세계 시장이나 금융시스템에 충격을 줄 수 있을 때만 10점으로 분류합니다. 일반 실적·기업 인수·지분 매각·규제 심사 보류·단순 지수 최고치와 구체적인 새 조치나 즉각적인 충격이 없는 산업 동향·전망은 최대 8점입니다.
- **알림 기준**: 중요도 7~8은 `breaking_news`, 9~10은 `breaking_news`와 `important_breaking_news` 구독자에게 중복 없이 발송합니다.
- **Pulse·중복 계약**: 비공개 `source_content` 원문은 Pulse 근거와 내부 중복 비교에만 사용합니다. 중복 DB 조회는 `content`를 읽지 않고 원문 없으면 제목만 비교합니다. Pulse의 원문 없는 옛 행은 상세 근거를 제공하지 않으며 생성요약을 원문으로 위장하지 않습니다. 기존 생성요약 보존/복원은 별도 비공개 아카이브·백업 승인 작업입니다. [제목 전용 배포 순서](docs/2026-10-10-headline-only-rollout.md)를 참고하세요.
- **필수 환경 변수 이름**: `GNEWS_API_KEY`, `OPENROUTER_API_KEY`, `SUPABASE_URL`, `SUPABASE_KEY`
- **선택 환경 변수 이름**: `BREAKING_NEWS_CONTENT_COLUMN_MODE`(`legacy|omit`, 기본 `legacy`), `GNEWS_AI_MODEL_NAME`, `GNEWS_AI_BACKUP_MODEL`, `GNEWS_DAILY_SAFETY_LIMIT`, `OPENROUTER_FREE_DAILY_LIMIT`, `OPENROUTER_BUDGET_PATH`, `REVALIDATE_SECRET`, `FRONTEND_URL`, `FIREBASE_CREDENTIALS`
- **OpenRouter 무료 요청 예산**: 무료 모델(`openrouter/free` 및 `:free`)은 기본 UTC 일일 990회와 5분 슬롯별 분할 한도를 공유합니다. 예산 DB 기본 경로는 프로젝트의 `.openrouter-budget.sqlite3`이며, 여러 프로세스가 같은 파일을 사용해야 사용량과 429 차단을 공유합니다. 예산 초과·429 차단은 현재 회차를 종료하며 기사는 다음 수집에서 다시 평가합니다.
- **OpenRouter 단계 토큰**: `GNEWS_SELECTION_MAX_TOKENS`와 기존 호환 이름 `GNEWS_SUMMARY_MAX_TOKENS`는 각각 선별·제목 단계 토큰 한도이며 기본값은 8192입니다. 무료 모델 제한과 유료 fallback 금지를 유지합니다.
- **수동 푸시 테스트**: `push_notification.py`를 직접 실행할 때는 `TEST_FCM_TOKEN` 환경변수로 대상 기기를 지정합니다. 토큰은 소스나 로그에 저장하지 않습니다.
- **OpenRouter 조사 기록**: 최종 2단계 구조는 [최신 SPEC](docs/superpowers/specs/2026-09-13-simple-two-stage-news-design.md)과 [조사 보고서](docs/openrouter-request-investigation.md)에 정리되어 있습니다. 이전 요청 예산 SPEC은 구현 이력으로 보존됩니다.
- **메모리 부족**: 현재 2GB Swap이 설정되어 있으나, 3개 이상의 코드를 돌릴 시 `free -h` 명령어로 여유 메모리를 꼭 체크하세요-
- **디스크 용량**: 20GB로 확장되었으므로 넉넉하지만, `df -h`에서 `Use%`가 80%를 넘지 않게 관리해 주세요-

---

## 7. nano .env

수정작업 완료 후
ctrl+o, 엔터, ctrl+x
