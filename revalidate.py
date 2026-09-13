import os
import requests
from dotenv import load_dotenv
from cycle_logging import log_event, record_failure

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
        record_failure("notify_failures", stage="notify", reason="missing_secret")
        log_event("revalidate_skipped", level=30, stage="notify", once_key="revalidate_secret_missing")
        return False
    
    try:
        url = f"{BASE_URL}/api/revalidate"
        params = {
            "secret": REVALIDATE_SECRET,
            "path": path
        }
        response = requests.get(url, params=params, timeout=10)
        if response.status_code == 200:
            log_event("revalidate_completed", level=10, stage="notify", status="ok")
            return True
        else:
            record_failure("notify_failures", stage="notify", reason="http_status")
            log_event("revalidate_failed", level=40, stage="notify", status_code=response.status_code, once_key="revalidate_http_failure")
            return False
    except Exception as e:
        record_failure("notify_failures", stage="notify", reason=type(e).__name__)
        log_event("revalidate_failed", level=40, stage="notify", error=type(e).__name__, once_key="revalidate_http_failure")
        return False

def revalidate_tag(tag):
    """
    Vercel에 특정 태그가 달린 데이터를 사용하는 페이지들을 다시 생성하도록 요청합니다.
    """
    if not REVALIDATE_SECRET:
        record_failure("notify_failures", stage="notify", reason="missing_secret")
        log_event("revalidate_skipped", level=30, stage="notify", once_key="revalidate_secret_missing")
        return False
    
    try:
        url = f"{BASE_URL}/api/revalidate"
        params = {
            "secret": REVALIDATE_SECRET,
            "tag": tag
        }
        response = requests.get(url, params=params, timeout=10)
        if response.status_code == 200:
            log_event("revalidate_completed", level=10, stage="notify", status="ok")
            return True
        else:
            record_failure("notify_failures", stage="notify", reason="http_status")
            log_event("revalidate_failed", level=40, stage="notify", status_code=response.status_code, once_key="revalidate_http_failure")
            return False
    except Exception as e:
        record_failure("notify_failures", stage="notify", reason=type(e).__name__)
        log_event("revalidate_failed", level=40, stage="notify", error=type(e).__name__, once_key="revalidate_http_failure")
        return False
