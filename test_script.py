import os
import json
from dotenv import load_dotenv
from supabase import create_client, Client
from push_notification import send_push_notification

# 환경 변수 로드
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# Supabase 클라이언트 초기화
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def test_manual_save():
    print("🧪 테스트 데이터를 DB에 저장하고 푸시 알림을 보냅니다...")
    
    test_item = {
        "title": "테스트 속보: 시스템 정상 작동 중! 🚀",
        "content": "이것은 시스템 작동 여부를 확인하기 위한 수동 테스트 메시지입니다. 현재 DB 저장과 푸시 알림 기능이 모두 정상입니다.",
        "importance_score": 10,
        "category": "market",
        "original_url": "https://finance.naver.com"
    }

    try:
        # 1. DB 저장 테스트
        res = supabase.table("breaking_news").insert({
            "title": test_item["title"],
            "content": test_item["content"],
            "importance_score": test_item["importance_score"],
            "category": test_item["category"],
            "original_url": test_item["original_url"]
        }).execute()
        
        print("✅ DB 저장 성공!")

        # 2. 푸시 알림 테스트 (카테고리 없이 전체 전송 테스트)
        prefix = "🚨[테스트]"
        send_push_notification(
            title=f"{prefix} {test_item['title']}",
            body=test_item['content'],
            url="/live"
        )
        print("✅ 푸시 알림 전송 명령 완료!")
        
    except Exception as e:
        print(f"❌ 테스트 실패: {e}")

if __name__ == "__main__":
    test_manual_save()
