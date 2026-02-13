# 📡 Hang on! 멀티 트래커 운영 가이드

이 가이드는 **경제 속보 트래커**와 **환율 급변 모니터링** 시스템의 통합 운영을 위해 작성되었습니다.

## 1. 서버 접속 및 환경 진입

서버에 접속한 후 파이썬 가상 환경을 활성화하는 기본 단계입니다-

```bash
# 1. 접속
https://console.cloud.google.com/

# 2. 프로젝트 폴더 이동
cd ~/hangon_breaknews

# 3. 가상 환경 활성화
source venv/bin/activate

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

# 3. PM2 프로세스 재시작 (변경사항 반영)
pm2 restart breaking-news
pm2 restart exchange-monitor

```

---

## 3. 모니터링 및 로그 확인

트래커들이 실시간으로 데이터를 잘 낚아오고 있는지 확인하는 방법입니다-

- **실시간 로그 확인 (하나씩 보기):**

```bash
pm2 logs breaking-news    # 뉴스 트래커 로그
pm2 logs exchange-monitor # 환율 트래커 로그

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
| `cat .env`             | 설정된 API 키 및 환경 변수 확인        |

---

## 6. 주의 사항

- **프로세스 명칭**: 기존 `tracker`는 `breaking-news`로 이름이 변경되었습니다-
- **메모리 부족**: 현재 2GB Swap이 설정되어 있으나, 3개 이상의 코드를 돌릴 시 `free -h` 명령어로 여유 메모리를 꼭 체크하세요-
- **디스크 용량**: 20GB로 확장되었으므로 넉넉하지만, `df -h`에서 `Use%`가 80%를 넘지 않게 관리해 주세요-

---
