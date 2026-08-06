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
- **DB 호환성**: 기존 `breaking_news` 테이블만 사용하므로 이번 교체에 DB 마이그레이션은 없습니다.
- **수집 시간창**: GNews 요청과 응답 검증 모두 최근 3시간 이내 작성 기사만 허용합니다.
- **사건 시간창**: 기사에 명시된 사건일이 작성일보다 3일 넘게 오래됐으면 제외합니다. 단, 오늘 시행·신규 집행·새 수치 같은 후속 사실과 미래 시행일은 유지합니다.
- **회차별 후보량**: 한국·미국·세계에서 각각 최대 25건, 총 최대 75건을 3회 요청으로 가져옵니다.
- **중복 기준**: 동일 URL과 최근 24시간 내 동일 사건을 차단하며, 승인·완료·취소처럼 상태가 달라진 후속 보도는 허용합니다. 동일 사건 뒤의 단순 시세 변화는 새 수치로 보지 않습니다. 같은 시장 하락의 지수·종목·산업 기사, 같은 분기 실적의 배당·순이익 기사, 같은 산업 정책의 세부 기사도 하나의 사건으로 묶습니다. AI에는 최신 100건만 비교 문맥으로 전달하고 최대 300건은 저장 전 규칙 기반으로 다시 검사합니다.
- **품질 기준**: 국가가 빠진 경제지표, 번역되지 않은 영문 일반어, 설명 없는 전문 약어·단위, 지표명이 빠진 퍼센트, 단순 상품 판매 기록·기업 순위·정기 신고 안내·지역 생활 서비스·통상적 중간관리자 선임·실적 발표 예고·소비자 설문·일반 전망·결과 없는 회의·개별 매장 결제 도입·일반 지수 등락·미실행 시험 계획·투자자 서한·미결정 서비스 검토·비구속적 업계 지침·경미한 코드셰어 확대·과거 주가 수익률 재소개·상업적 성과 없는 소비재 신제품·미래 소비자 수수료·단순 통화 안정세·근거 없는 전문가 경고·경미한 지역 과징금은 저장하지 않습니다. 펀드 수익률이나 거래 주체를 다른 기업으로 바꿔 쓴 요약, 도달 수치와 증가분을 혼동한 표현, 서로 모순되는 연도별 매출 수치도 저장 전에 보정하거나 차단합니다.
- **속보 기준**: 기사 종류와 관계없이 영향 범위·변화 규모·시장 즉시성 중 두 가지 이상을 강하게 충족하면 9점 속보, 세 가지 모두 충족하며 세계 시장이나 금융시스템에 충격을 줄 수 있을 때만 10점으로 분류합니다. 일반 실적·기업 인수·지분 매각·규제 심사 보류와 단순 지수 최고치는 최대 8점입니다.
- **알림 기준**: 중요도 7~8은 `breaking_news`, 9~10은 `breaking_news`와 `important_breaking_news` 구독자에게 중복 없이 발송합니다.
- **필수 환경 변수 이름**: `GNEWS_API_KEY`, `OPENROUTER_API_KEY`, `SUPABASE_URL`, `SUPABASE_KEY`
- **선택 환경 변수 이름**: `GNEWS_AI_MODEL_NAME`, `GNEWS_AI_BACKUP_MODEL`, `GNEWS_DAILY_SAFETY_LIMIT`, `REVALIDATE_SECRET`, `FRONTEND_URL`, `FIREBASE_CREDENTIALS`
- **메모리 부족**: 현재 2GB Swap이 설정되어 있으나, 3개 이상의 코드를 돌릴 시 `free -h` 명령어로 여유 메모리를 꼭 체크하세요-
- **디스크 용량**: 20GB로 확장되었으므로 넉넉하지만, `df -h`에서 `Use%`가 80%를 넘지 않게 관리해 주세요-

---

## 7. nano .env

수정작업 완료 후
ctrl+o, 엔터, ctrl+x
