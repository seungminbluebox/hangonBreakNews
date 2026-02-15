import os
import requests
from dotenv import load_dotenv

# .env 파일의 절대 경로를 찾아 로드합니다.
env_path = os.path.join(os.path.dirname(__file__), '.env')
load_dotenv(env_path)

REVALIDATE_SECRET = os.getenv("REVALIDATE_SECRET")
# 기본 URL 수정: 사용자가 성공한 www 주소를 기본값으로 사용
BASE_URL = os.getenv("FRONTEND_URL", "https://www.hangon.co.kr").rstrip('/')

def revalidate_path(path):
    """
    Vercel에 특정 경로의 페이지를 다시 생성하도록 요청합니다.
    """
    if not REVALIDATE_SECRET:
        print("⚠ REVALIDATE_SECRET이 설정되지 않았습니다. 갱신을 건너뜁니다.")
        return False
    
    try:
        url = f"{BASE_URL}/api/revalidate"
        params = {
            "secret": REVALIDATE_SECRET,
            "path": path
        }
        # 요청 보내기 전 URL 확인용 출력 (보안상 secret은 가림)
        print(f"📡 갱신 요청 중: {url}?path={path}")
        
        response = requests.get(url, params=params, timeout=10)
        if response.status_code == 200:
            print(f"✅ 성공적으로 경로를 갱신했습니다: {path}")
            return True
        else:
            print(f"❌ 경로 갱신 실패: {path}, 상태 코드: {response.status_code}")
            # 에러 상세 내용 출력 (디버깅용)
            try:
                print(f"📄 응답 내용: {response.text[:100]}")
            except:
                pass
            return False
    except Exception as e:
        print(f"❌ Revalidate API 호출 중 오류 발생: {e}")
        return False

def revalidate_tag(tag):
    """
    Vercel에 특정 태그가 달린 데이터를 사용하는 페이지들을 다시 생성하도록 요청합니다.
    """
    if not REVALIDATE_SECRET:
        print("⚠ REVALIDATE_SECRET이 설정되지 않았습니다. 갱신을 건너뜁니다.")
        return False
    
    try:
        url = f"{BASE_URL}/api/revalidate"
        params = {
            "secret": REVALIDATE_SECRET,
            "tag": tag
        }
        # 요청 보내기 전 URL 확인용 출력
        print(f"📡 태그 갱신 요청 중: {url}?tag={tag}")

        response = requests.get(url, params=params, timeout=10)
        if response.status_code == 200:
            print(f"✅ 성공적으로 태그를 갱신했습니다: {tag}")
            return True
        else:
            print(f"❌ 태그 갱신 실패: {tag}, 상태 코드: {response.status_code}")
            try:
                print(f"📄 응답 내용: {response.text[:100]}")
            except:
                pass
            return False
    except Exception as e:
        print(f"❌ Revalidate API 호출 중 오류 발생: {e}")
        return False
