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
from revalidate import revalidate_path

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
        self.REFERENCE_HOUR = 9 # 오전 9시 기준
        self.STEP_UNIT = 10     # 10원 단위 알림
        
        # 오늘 알림을 보낸 단계들을 저장하는 Set (파일 저장 없이 메모리 관리)
        self.notified_steps = set() # 예: {1, 2, -1}
        self.last_notified_date = None

    def check_and_notify(self):
        now = datetime.now(self.kst)
        today = now.date()
        current_price = get_usd_krw()
        
        if current_price is None:
            return

        # 1. 9시 기준가 및 알림 기록 초기화 (자정 대신 오전 9시에만 초기화)
        # 9시 0분 0초~59초 사이에 단 한 번만 실행되도록 보장
        if now.hour == self.REFERENCE_HOUR and now.minute == 0:
            if self.last_notified_date != today: # 오늘 아직 9시 초기화 전이라면
                self.daily_base_price = current_price
                self.notified_steps.clear() 
                self.last_notified_date = today
                print(f"🌅 [{now.strftime('%H:%M:%S')}] 오늘의 9시 기준환율 설정 및 기록 초기화: {self.daily_base_price:.2f}원")
                return # 초기화 직후에는 알림 체크 건너뜀 (변동폭이 0이므로)

        # 프로그램 첫 실행 시 기준가 설정
        if self.daily_base_price is None:
            self.daily_base_price = current_price
            self.last_notified_date = today # 초기 실행 시점의 날짜 기록
            
            # [수정] 첫 실행 시 현재 가격을 기준으로 모든 '현재 상태'의 단계를 이미 알림한 것으로 간주
            # 이렇게 하면 시작하자마자 알림이 오는 것을 방지할 수 있습니다.
            diff = current_price - self.daily_base_price
            current_step = int(diff / self.STEP_UNIT)
            if current_step != 0:
                self.notified_steps.add(current_step)
                
            print(f"📌 모니터링 시작 (현재 기준가: {self.daily_base_price:.2f}원)")
            return # 첫 실행 시에는 설정만 하고 다음 턴부터 체크

        # 2. 변동 폭 및 단계(Step) 계산
        diff = current_price - self.daily_base_price
        # int(diff / 10) 방식으로 음수(-0.1원)가 -1단계로 처리되는 것을 방지 (0에 가깝게 버림)
        current_step = int(diff / self.STEP_UNIT)
        
        # 0단계(10원 미만 변동)는 무시
        if current_step == 0:
            print(f"[{now.strftime('%H:%M:%S')}] 현재: {current_price:.2f}원 (변동: {diff:+.1f}원)")
            return

        # 3. 알림 중복 체크 (Set 활용)
        if current_step not in self.notified_steps:
            direction_str = "상승 📈" if diff > 0 else "하락 📉"
            abs_step_val = abs(current_step) * self.STEP_UNIT
            
            title = f"환율 {direction_str} ({abs_step_val}원 이상 변동)"
            body = f"현재: {current_price:.2f}원 (오전 9시 기준가 대비 {diff:+.1f}원)"
            
            print(f"🔔 알림 발송: {title}")
            
            send_push_notification(
                title=title,
                body=body,
                url="/currency-desk",
                category="common_currency" 
            )
            
            revalidate_path("/currency-desk")
            
            # 이 단계(current_step)를 기록하여 오늘 다시는 알리지 않음
            self.notified_steps.add(current_step)
        else:
            # 이미 알림을 보낸 단계에서의 미세한 변동(노이즈)은 로그만 출력하거나 무시
            pass

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