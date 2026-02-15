import os
import sys
import json
from datetime import datetime, timedelta
from pywebpush import webpush, WebPushException
from supabase import create_client, Client
from dotenv import load_dotenv

# 같은 폴더 혹은 상위 폴더의 revalidate 가져오기
sys.path.append(os.path.abspath(os.path.dirname(__file__)))
try:
    from revalidate import revalidate_path
except ImportError:
    def revalidate_path(path): pass

# .env 파일 로드
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
VAPID_PRIVATE_KEY = os.getenv("VAPID_PRIVATE_KEY")
VAPID_CLAIMS = {
    "sub": "mailto:boxmagic25@gmail.com"    
}

def is_quiet_time():
    """현재 한국 시간(KST)이 에티켓 시간(00:00~09:00)인지 확인"""
    # UTC 기준 현재 시간에서 9시간 더하기 (KST)
    now_kst = datetime.utcnow() + timedelta(hours=9)
    return 0 <= now_kst.hour < 9

def send_push_notification(title, body, url="/", category=None):
    """
    특정 카테고리를 구독한 사용자에게만 푸시 알림을 전송합니다.
    에티켓 모드가 활성화된 사용자는 밤 시간대에 알림을 보관함(Queue)으로 보냅니다.
    """
    if not VAPID_PRIVATE_KEY:
        print("VAPID_PRIVATE_KEY가 설정되지 않았습니다.")
        return

    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    
    # 구독 정보 가져오기 (카테고리 필터링 적용)
    try:
        query = supabase.table("push_subscriptions").select("*")
        
        if category:
            query = query.eq(f"preferences->>{category}", "true")
            print(f"카테고리 필터링 적용: {category}")
            
        response = query.execute()
        subscriptions = response.data
    except Exception as e:
        print(f"구독 정보를 불러오는 중 에러 발생: {e}")
        return

    quiet_mode = is_quiet_time()
    if quiet_mode:
        print(f"현재 에티켓 시간대입니다. ( {len(subscriptions)}명의 대상자에게 알림 처리를 시작합니다.)")
    else:
        print(f"현재 활동 시간대입니다. ( {len(subscriptions)}명의 대상자에게 알림 처리를 시작합니다.)")

    # 알림 전송과 동시에 관련 페이지 캐시 갱신 (Vercel 최적화)
    if url:
        revalidate_path(url)
        # 메인 페이지도 함께 갱신 (마켓 티커 등 업데이트용)
        if url != "/":
            revalidate_path("/")

    for sub_record in subscriptions:
        try:
            prefs = sub_record.get("preferences", {})
            etiquette_enabled = prefs.get("etiquette_mode", False)

            # 에티켓 모드가 켜져 있고 현재가 밤 시간대라면 큐에 저장
            if etiquette_enabled and quiet_mode:
                supabase.table("notification_queue").insert([{
                    "subscription_id": sub_record["id"],
                    "title": title,
                    "body": body,
                    "url": url
                }]).execute()
                print(f"에티켓 모드: 알림 보류 및 큐 저장 (ID: {sub_record['id']})")
                continue

            subscription_info = sub_record["subscription"]
            
            webpush(
                subscription_info=subscription_info,
                data=json.dumps({
                    "title": title,
                    "body": body,
                    "url": url
                }),
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims=VAPID_CLAIMS.copy(),
                ttl=86400,
                headers={"Urgency": "high"}
            )
            print(f"알림 전송 성공: {sub_record['id']}")
        except WebPushException as ex:
            if ex.response is not None:
                print(f"알림 전송 실패 (ID: {sub_record['id']}, Status: {ex.response.status_code})")
                if ex.response.status_code in [404, 410]:
                    supabase.table("push_subscriptions").delete().eq("id", sub_record["id"]).execute()
                    print(f"만료된 구독 삭제됨: {sub_record['id']}")
            else:
                print(f"알림 전송 실패 (ID: {sub_record['id']}): {ex}")
        except Exception as e:
            print(f"알림 전송 중 에러 발생: {e}")

def send_push_to_all(title, body, url="/"):
    """기존 함수 유지 (내부적으로 전체 전송 호출)"""
    send_push_notification(title, body, url)

if __name__ == "__main__":
    # 테스트용
    now = datetime.now()
    date_str = f"{now.month}월 {now.day}일"
    # 예시: 데일리 업데이트 카테고리로 발송 테스트
    send_push_notification("Hang on!", f"{date_str} 새로운 경제 리포트가 업데이트되었습니다.", "/news/daily-report", category="daily_update")
