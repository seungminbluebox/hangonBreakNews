import os
import sys
import json
import time
from datetime import datetime, timedelta
from supabase import create_client, Client
from dotenv import load_dotenv

import firebase_admin
from firebase_admin import credentials, messaging

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

# Firebase Admin SDK 초기화 (앱이 아직 초기화되지 않았을 때만)
if not firebase_admin._apps:
    print(f"[DEBUG] Firebase 초기화 시도 중... (현재 작업 디렉토리: {os.getcwd()})")
    try:
        firebase_credentials_env = os.getenv("FIREBASE_CREDENTIALS")
        
        # 실제 파일명 상수로 정의
        FIREBASE_KEY_FILENAME = 'hangonalarm-firebase-adminsdk-fbsvc-a0ddf6e01d.json'
        
        # 1. 현재 폴더(hangonBreakNews)에서 검색
        current_dir = os.path.dirname(os.path.abspath(__file__))
        key_path = os.path.join(current_dir, FIREBASE_KEY_FILENAME)
        
        # 2. 없다면 메인 백엔드(hangonbackend) 폴더경로로 대체하여 검색 (상대경로 활용)
        if not os.path.exists(key_path):
            fallback_path = os.path.abspath(os.path.join(current_dir, "../../hangon/hangonbackend", FIREBASE_KEY_FILENAME))
            if os.path.exists(fallback_path):
                key_path = fallback_path

        if firebase_credentials_env:
            # 환경변수에서 JSON 로드 (우선순위 1)
            print("[DEBUG] 환경변수 FIREBASE_CREDENTIALS를 발견했습니다. 파싱 시도...")
            cred_dict = json.loads(firebase_credentials_env)
            cred = credentials.Certificate(cred_dict)
            firebase_admin.initialize_app(cred)
            print("✅ Firebase Admin 초기화 성공! (환경변수 FIREBASE_CREDENTIALS 사용)")
        elif os.path.exists(key_path):
            # 파일에서 로드 (우선순위 2)
            print(f"[DEBUG] JSON 키 파일을 발견했습니다: {key_path}")
            cred = credentials.Certificate(key_path)
            firebase_admin.initialize_app(cred)
            print(f"✅ Firebase Admin 초기화 성공! (경로: {key_path})")
        else:
            print(f"❌ 오류: FIREBASE_CREDENTIALS 환경변수도 없고, {FIREBASE_KEY_FILENAME} 파일도 찾을 수 없습니다.")
    except Exception as e:
        print(f"❌ Firebase Admin 초기화 실패 상세 에러: {e}")

def is_quiet_time():
    """현재 한국 시간(KST)이 에티켓 시간(00:00~09:00)인지 확인"""
    # UTC 기준 현재 시간에서 9시간 더하기 (KST)
    now_kst = datetime.utcnow() + timedelta(hours=9)
    return 0 <= now_kst.hour < 9

def send_push_notification(title, body, url="/", category=None):
    """
    특정 카테고리를 구독한 사용자에게 푸시 알림을 전송합니다.
    (새로운 fcm_subscriptions 테이블 및 Firebase Admin Multicast 적용)
    """
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    
    # 구독 정보 가져오기 (카테고리 필터링 적용)
    try:
        # 기존 push_subscriptions 대신 새로운 fcm_subscriptions 테이블만 조회합니다.
        query = supabase.table("fcm_subscriptions").select("*")
        
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
        print(f"현재 에티켓 시간대입니다. ( {len(subscriptions)}명의 대상자에게 필터링 후 발송 처리합니다.)")
    else:
        print(f"현재 활동 시간대입니다. ( {len(subscriptions)}명의 대상자에게 발송 처리합니다.)")

    # 알림 전송과 동시에 관련 페이지 캐시 갱신 (Vercel 최적화)
    if url:
        revalidate_path(url)
        if url != "/":
            revalidate_path("/")

    fcm_tokens_to_send = []
    fcm_token_to_id_map = {}
    queue_inserts = []

    # 1. 모든 유저 정보 순회 (에티켓 모드 확인 및 토큰 추출)
    for sub_record in subscriptions:
        try:
            fcm_token = sub_record.get("fcm_token")
            prefs = sub_record.get("preferences", {})
            etiquette_enabled = prefs.get("etiquette_mode", False)

            # 에티켓 모드가 켜져 있고 현재가 밤 시간대인 경우, 속보 알림은 큐에 저장하지 않고 조용히 취소(무시)합니다.
            if etiquette_enabled and quiet_mode:
                if category in ["breaking_news", "important_breaking_news"]:
                    print(f"에티켓 모드: 속보 알림( {category} ) 전송 안 함 (ID: {sub_record['id']})")
                    continue

            # 전송 대상에 포함
            if fcm_token:
                fcm_tokens_to_send.append(fcm_token)
                fcm_token_to_id_map[fcm_token] = sub_record["id"]

        except Exception as e:
            print(f"유저 데이터 필터링 중 에러 (ID: {sub_record.get('id')}): {e}")

    # 2. Firebase Cloud Messaging(FCM)을 통한 초고속 대량 발송 (Multicast)
    if fcm_tokens_to_send:
        print(f"-> FCM 멀티캐스트 방식으로 {len(fcm_tokens_to_send)}명에게 동시 발송합니다...")
        
        chunk_size = 500
        success_count = 0
        failure_count = 0
        
        for i in range(0, len(fcm_tokens_to_send), chunk_size):
            token_chunk = fcm_tokens_to_send[i:i + chunk_size]
            
            # 고유 태그 설정 (각 알림이 독립적으로 쌓이도록 타임스탬프 추가)
            notification_tag = f"hangon-{category if category else 'upd'}-{int(time.time() * 1000)}"
            
            # 메시지 구성 (완벽한 Data-only 메시지로 전환하여 중복 발생 방지)
            message = messaging.MulticastMessage(
                tokens=token_chunk,
                data={
                    "title": title,
                    "body": body,
                    "url": url,
                    "icon": "/icon-192.png",
                    "badge": "/badge-72x72.png",
                    "tag": notification_tag
                },
                android=messaging.AndroidConfig(
                    priority='high'
                ),
                webpush=messaging.WebpushConfig(
                    headers={
                        "Urgency": "high"
                    }
                )
            )
            
            try:
                response = messaging.send_each_for_multicast(message)
                success_count += response.success_count
                failure_count += response.failure_count
                
                # 실패한 경우의 삭제 처리 루틴 (토큰 만료 등)
                if response.failure_count > 0:
                    for idx, resp in enumerate(response.responses):
                        if not resp.success:
                            failed_token = token_chunk[idx]
                            sub_id = fcm_token_to_id_map[failed_token]
                            # 에러 코드가 NOT_FOUND 이거나 UNREGISTERED 인 경우 구독 효력 상실
                            if resp.exception and resp.exception.code in ['NOT_FOUND', 'UNREGISTERED', 'INVALID_ARGUMENT']:
                                supabase.table("fcm_subscriptions").delete().eq("id", sub_id).execute()
                                print(f"   FCM 삭제됨(만료): {sub_id}")
            except Exception as e:
                print(f"   FCM 일괄 전송 중 통신 에러 발생: {e}")
                
        print(f"발송 최종 완료: 성공 {success_count}건 / 실패 {failure_count}건")

def send_push_to_all(title, body, url="/"):
    """기존 함수 유지 (내부적으로 전체 전송 호출)"""
    send_push_notification(title, body, url)

if __name__ == "__main__":
    # 테스트용
    now = datetime.now()
    date_str = f"{now.month}월 {now.day}일"
    send_push_notification("Hang on 파이프라인 테스트", f"{date_str} 자동화 스크립트 기반 푸시 테스트 발송", "/news", category="breaking_news")
