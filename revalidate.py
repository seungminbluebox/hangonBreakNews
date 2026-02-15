import os
import requests
from dotenv import load_dotenv

load_dotenv()

REVALIDATE_SECRET = os.getenv("REVALIDATE_SECRET")
BASE_URL = os.getenv("FRONTEND_URL", "https://www.hangon.co.kr")

def revalidate_path(path):
    if not REVALIDATE_SECRET:
        print("⚠ REVALIDATE_SECRET이 설정되지 않았습니다.")
        return False
    
    try:
        url = f"{BASE_URL}/api/revalidate"
        params = {
            "secret": REVALIDATE_SECRET,
            "path": path
        }
        response = requests.get(url, params=params)
        if response.status_code == 200:
            print(f"✅ 성공적으로 경로를 갱신했습니다: {path}")
            return True
        else:
            print(f"❌ 경로 갱신 실패: {path}, 상태 코드: {response.status_code}")
            return False
    except Exception as e:
        print(f"❌ Revalidate API 호출 중 오류 발생: {e}")
        return False
