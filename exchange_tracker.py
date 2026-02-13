import os
import sys
import time
from datetime import datetime
import pytz
from dotenv import load_dotenv
import FinanceDataReader as fdr

# 1. 레포지토리 환경 설정 및 push_notification 가져오기
sys.path.append(os.path.abspath(os.path.dirname(__file__)))
from push_notification import send_push_notification

load_dotenv()

def get_usd_krw():
    """FinanceDataReader를 사용하여 실시간 원/달러 환율 수집"""
    try:
        # 'USD/KRW' 환율 데이터 가져오기 (가장 최근 데이터)
        df = fdr.DataReader('USD/KRW')
        current_price = df['Close'].iloc[-1]
        return float(current_price)
    except Exception as e:
        print(f"❌ 환율 수집 에러 (FDR): {e}")
        return None

class ExchangeMonitor:
    def __init__(self):
        self.kst = pytz.timezone('Asia/Seoul')
        self.daily_base_price = None
        self.last_step = 0      # 마지막으로 알림을 보낸 단계
        self.REFERENCE_HOUR = 9 # 오전 9시 기준
        self.STEP_UNIT = 10     # 10원 단위 알림

    def check_and_notify(self):
        now = datetime.now(self.kst)
        current_price = get_usd_krw()
        
        if current_price is None:
            return

        # 1. 매일 오전 9시 정각에 기준가 초기화
        if now.hour == self.REFERENCE_HOUR and now.minute == 0:
            if self.daily_base_price != current_price:
                self.daily_base_price = current_price
                self.last_step = 0
                # [수정] 소수점 둘째 자리까지만 표시 (.2f)
                print(f"🌅 [{now.strftime('%H:%M:%S')}] 오늘의 9시 기준환율 설정: {self.daily_base_price:.2f}원")

        # 기준가가 아직 설정되지 않은 경우 (프로그램 첫 실행 시)
        if self.daily_base_price is None:
            self.daily_base_price = current_price
            # [수정] 소수점 둘째 자리까지만 표시 (.2f)
            print(f"📌 모니터링 시작 (현재 기준가: {self.daily_base_price:.2f}원)")

        # 2. 변동 폭 및 단계(Step) 계산
        diff = current_price - self.daily_base_price
        current_step = int(abs(diff) // self.STEP_UNIT)

        # 3. 새로운 10원 계단(Step)에 진입했을 때만 알림 발송
        if current_step != self.last_step and current_step > 0:
            direction = "상승 📈" if diff > 0 else "하락 📉"
            title = f"환율 {direction} ({current_step * self.STEP_UNIT}원 이상 변동)"
            body = f"현재: {current_price:.2f}원 (오전 9시 기준가 대비 {diff:+.1f}원)"
            
            print(f"🔔 알림 발송: {title}")
            
            send_push_notification(
                title=title,
                body=body,
                url="/currency-desk",
                category="common_currency" 
            )
            
            self.last_step = current_step
        else:
            # 실시간 상태 로그 (1분마다 출력)
            print(f"[{now.strftime('%H:%M:%S')}] 현재: {current_price:.2f}원 (9시기준 변동: {diff:+.1f}원)")

def run():
    print("🚀 환율 10원 계단형 감시 시스템 가동 (FinanceDataReader Ver.)")
    monitor = ExchangeMonitor()
    
    while True:
        try:
            monitor.check_and_notify()
            # 1분 주기로 체크
            time.sleep(60)
        except KeyboardInterrupt:
            print("\n🛑 중단되었습니다.")
            break
        except Exception as e:
            print(f"💀 에러 발생 (60초 후 재시도): {e}")
            time.sleep(60)

if __name__ == "__main__":
    run()