import os
import json
from datetime import datetime
from pywebpush import webpush, WebPushException
from supabase import create_client, Client
from dotenv import load_dotenv

# .env 파일 로드
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
VAPID_PRIVATE_KEY = os.getenv("VAPID_PRIVATE_KEY")
VAPID_CLAIMS = {
    "sub": "mailto:boxmagic25@gmail.com"
}

def send_push_to_all(title, body, url="/"):
    if not VAPID_PRIVATE_KEY:
        print("VAPID_PRIVATE_KEY가 설정되지 않았습니다.")
        return

    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    
    # 구독 정보 가져오기
    try:
        response = supabase.table("push_subscriptions").select("*").execute()
        subscriptions = response.data
    except Exception as e:
        print(f"구독 정보를 불러오는 중 에러 발생: {e}")
        return

    print(f"총 {len(subscriptions)}명의 구독자에게 알림을 전송합니다.")

    for sub_record in subscriptions:
        try:
            subscription_info = sub_record["subscription"]
            
            webpush(
                subscription_info=subscription_info,
                data=json.dumps({
                    "title": title,
                    "body": body,
                    "url": url
                }),
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims=VAPID_CLAIMS.copy()
            )
            print(f"알림 전송 성공: {sub_record['id']}")
        except WebPushException as ex:
            print(f"알림 전송 실패 (ID: {sub_record['id']}): {ex}")
            # 만약 구독이 만료되었거나 잘못된 경우 DB에서 삭제 처리
            if ex.response and ex.response.status_code in [404, 410]:
                supabase.table("push_subscriptions").delete().eq("id", sub_record["id"]).execute()
                print(f"만료된 구독 삭제됨: {sub_record['id']}")
        except Exception as e:
            print(f"알림 전송 중 기타 에러 발생: {e}")

if __name__ == "__main__":
    # 테스트용
    now = datetime.now()
    date_str = f"{now.month}월 {now.day}일"
    send_push_to_all(
        title="[속보] 시스템 가동 테스트 🚀", 
        body=f"{date_str} 실시간 뉴스 알림 시스템이 정상적으로 시작되었습니다.", 
        url="/live"
    )
