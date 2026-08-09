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
- **DB 호환성**: 기존 `breaking_news` 테이블과 `title`, `content`, `importance_score`, `category`, `original_url` 저장 계약을 그대로 사용합니다. 새 테이블이나 정기 SQL 마이그레이션은 없습니다.
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
- **회차별 후보량**: 한국·미국·세계에서 각각 최대 25건, 총 최대 75건을 3회 요청으로 가져옵니다.
- **중복 기준**: 동일 URL과 최근 24시간 내 동일 사건을 차단하며, 승인·완료·취소·공식 수치 수정처럼 상태가 달라진 후속 보도는 허용합니다. 동일 사건 뒤의 단순 시세 변화는 새 수치로 보지 않습니다. 같은 시장 하락의 지수·종목·산업 기사, 같은 분기 실적의 배당·순이익 기사, 같은 산업 정책의 세부 기사도 하나의 사건으로 묶습니다. 고용지표·실업률·일자리처럼 표현이 달라도 국가·발표 기간·핵심 수치가 모두 일치할 때만 같은 발표로 묶으며, 하나라도 다르면 별개 소식으로 보존합니다. AI에는 최신 100건만 비교 문맥으로 전달하고 최대 300건은 저장 전 규칙 기반으로 다시 검사합니다.
- **품질 기준**: 기업·지역 규모가 작더라도 가격 변경·과징금·상장 유지 조치·자산 거래·공장 폐쇄·고용 변화처럼 구체적인 주식·경제 사건이면 후보로 유지합니다. 반면 국가가 빠진 경제지표, 번역되지 않은 영문 일반어, 설명 없는 전문 약어·단위, 단순 상품 홍보·기업 순위·결과 없는 회의·새 사실 없는 시황은 저장하지 않습니다. 잘린 제목, 제목·본문의 상승·하락 방향 반전, 제목에만 있는 핵심 숫자, 깨진 한국어 금액 단위, 거래 주체·인과관계·증거 수준이 원문과 달라진 요약은 저장 전에 차단합니다.
- **품질 재시도**: AI가 선택한 기사의 제목·요약만 품질 검사에 실패하면 같은 호출 흐름에서 해당 기사만 한 번 보정합니다. 그래도 실패하면 프로세스 메모리에만 보류하며, 다음 사이클은 새 기사를 먼저 처리한 뒤 이전 실패 최대 10건을 처리합니다. 세 번째 미해결 시 종료합니다. 재시도 횟수나 보정 초안은 DB에 저장하지 않으며 프로세스 재시작 시 사라집니다.
- **속보 기준**: 기사 종류와 관계없이 영향 범위·변화 규모·시장 즉시성 중 두 가지 이상을 강하게 충족하면 9점 속보, 세 가지 모두 충족하며 세계 시장이나 금융시스템에 충격을 줄 수 있을 때만 10점으로 분류합니다. 일반 실적·기업 인수·지분 매각·규제 심사 보류·단순 지수 최고치와 구체적인 새 조치나 즉각적인 충격이 없는 산업 동향·전망은 최대 8점입니다.
- **알림 기준**: 중요도 7~8은 `breaking_news`, 9~10은 `breaking_news`와 `important_breaking_news` 구독자에게 중복 없이 발송합니다.
- **Pulse 계약**: Pulse가 제목 목록으로 후보를 고르고 선택된 기사의 `content`와 `original_url`을 읽는 기존 흐름은 유지됩니다. `policy` 추가와 재시도 메타데이터는 이 계약을 바꾸지 않습니다.
- **필수 환경 변수 이름**: `GNEWS_API_KEY`, `OPENROUTER_API_KEY`, `SUPABASE_URL`, `SUPABASE_KEY`
- **선택 환경 변수 이름**: `GNEWS_AI_MODEL_NAME`, `GNEWS_AI_BACKUP_MODEL`, `GNEWS_DAILY_SAFETY_LIMIT`, `REVALIDATE_SECRET`, `FRONTEND_URL`, `FIREBASE_CREDENTIALS`
- **메모리 부족**: 현재 2GB Swap이 설정되어 있으나, 3개 이상의 코드를 돌릴 시 `free -h` 명령어로 여유 메모리를 꼭 체크하세요-
- **디스크 용량**: 20GB로 확장되었으므로 넉넉하지만, `df -h`에서 `Use%`가 80%를 넘지 않게 관리해 주세요-

---

## 7. nano .env

수정작업 완료 후
ctrl+o, 엔터, ctrl+x
