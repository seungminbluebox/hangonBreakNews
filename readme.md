# 📡 Hang on! Breaking News Tracker 운영 가이드

## 1. 서버 접속 및 환경 진입

서버에 접속한 후 파이썬 가상 환경을 활성화하는 기본 단계

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

로컬에서 수정한 코드를 서버에 반영하고, 새로운 라이브러리를 설치하는 과정이다-

```bash
# 1. 최신 코드 가져오기 (GitHub)
git pull origin main

# 2. 새로운 라이브러리 설치 (필요시)
pip install -r requirements.txt

# 3. PM2 프로세스 재시작 (변경사항 반영)
pm2 restart tracker

```

---

## 3. 모니터링 및 로그 확인

트래커가 실시간으로 뉴스를 잘 낚아오고 있는지 확인하는 방법이다-

- **실시간 로그 확인:**

```bash
pm2 logs tracker

```

_(나가려면 `Ctrl + C`를 누른다-)_

- **프로세스 상태 요약:**

```bash
pm2 status

```

- **에러 로그만 모아보기:**

```bash
pm2 logs tracker --err

```

---

## 4. 주요 관리 명령어 요약

| 명령어                                            | 설명                              |
| ------------------------------------------------- | --------------------------------- |
| `pm2 start ...`                                   | 트래커 서비스 최초 실행           |
| `pm2 logs tracker --lines 200`                    | 200줄씩 로그 조회                 |
| `pm2 stop tracker`                                | 트래커 일시 중지                  |
| `pm2 restart tracker`                             | 트래커 재시작 (설정 변경 시 필수) |
| `pm2 save`                                        | 현재 실행 상태 저장 (재부팅 대비) |
| `cat .env`                                        | 현재 설정된 API 키 확인           |
| `less /home/boxmagic25/.pm2/logs/tracker-out.log` | 페이지 단위로 로그보기            |

---

## 5. 주의 사항

- **환경 변수:** `.env` 파일은 보안상 깃허브에 올리지 않으며, 서버에서 직접 관리한다-
- **의존성 충돌:** `websockets` 버전은 항상 `15.0`대를 유지해야 `google-genai`와 충돌하지 않는다-
- **메모리 관리:** `e2-micro` 인스턴스 사용 시 Swap 메모리 설정이 유지되고 있는지 주기적으로 확인한다-

---
