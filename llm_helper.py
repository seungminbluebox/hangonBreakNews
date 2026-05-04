import os
import time
import random
import requests
import re
from dotenv import load_dotenv

load_dotenv()

class DummyResponse:
    """기존 파이썬 Gemini SDK의 response.text 프로퍼티와 호환성을 맞추기 위한 클래스"""
    def __init__(self, text):
        self.text = text

def safe_generate_content(prompt_text, max_retries=10):
    """
    OpenRouter API 브로커 (DeepSeek V3 메인 + Gemini 2.5 Flash 백업)
    """
    # 환경 변수 및 설정
    AI_MODEL_NAME = os.getenv("OPENROUTER_MODEL_NAME", "nvidia/nemotron-3-super-120b-a12b:free")#google/gemini-2.5-flash-lite
    BACKUP_MODEL_NAME = os.getenv("OPENROUTER_BACKUP_MODEL", "google/gemini-2.5-flash")
    OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

    if not OPENROUTER_API_KEY:
        raise ValueError("OPENROUTER_API_KEY가 환경변수에 등록되지 않았습니다.")
        
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://www.hangon.co.kr",
        "X-Title": "Hangon breaking_tracker",
    }
    
    # 속보 분석용 프롬프트는 묶음 처리 요청이므로 JSON 반환을 강제함
    enforced_prompt = prompt_text + "\n\n(IMPORTANT: 응답은 반드시 마크다운 백틱(```json) 없이 순수한 JSON 텍스트로만 반환하세요. 만약 사고 과정(Thought)이 포함된다면 반드시 마지막에 JSON만 출력하세요.)"

    # 응답에서 JSON만 추출하는 내부 함수
    def extract_json_payload(text):
        try:
            # 1. 마크다운 백틱 제거 시도
            match = re.search(r'```(?:json)?\s*(.*?)\s*```', text, re.DOTALL | re.IGNORECASE)
            if match:
                return match.group(1).strip()
            
            # 2. 첫 번째 '[' 와 마지막 ']' 사이 추출 (배열)
            start_idx = text.find('[')
            end_idx = text.rfind(']')
            if start_idx != -1 and end_idx != -1:
                return text[start_idx:end_idx+1].strip()
            
            # 3. 첫 번째 '{' 와 마지막 '}' 사이 추출 (객체)
            start_idx = text.find('{')
            end_idx = text.rfind('}')
            if start_idx != -1 and end_idx != -1:
                return text[start_idx:end_idx+1].strip()
                
            return text.strip()
        except:
            return text.strip()

    for attempt in range(max_retries):
        # 첫 2회까지는 메인 모델, 그 이후는 백업 모델 시도
        current_model = AI_MODEL_NAME if attempt < 2 else BACKUP_MODEL_NAME
        
        data = {
            "model": current_model,
            "messages": [
                {"role": "user", "content": enforced_prompt}
            ],
            "max_tokens": 5000,
            "temperature": 0.2
        }
        
        try:
            res = requests.post(url, headers=headers, json=data, timeout=120)
            res.raise_for_status() 
            
            result_json = res.json()
            if 'choices' not in result_json:
                print(f"⚠️ API 응답에 'choices'가 없습니다: {result_json}")
                raise KeyError('choices missing in OpenRouter response')
                
            raw_content = result_json['choices'][0]['message']['content'].strip()
            
            # 사고 과정이나 마크다운이 섞여있어도 JSON만 정교하게 추출
            content_text = extract_json_payload(raw_content)
            if not content_text:
                raise ValueError("JSON 추출 결과가 비어 있습니다.")
                
            return DummyResponse(content_text)
            
        except requests.exceptions.RequestException as e:
            error_msg = str(e).lower()
            if hasattr(e, 'response') and e.response is not None:
                error_msg += f" (Status: {e.response.status_code}) {e.response.text}"
                
            wait_time = random.uniform(3, 8) * (attempt + 1)
            print(f"⚠️ [속보 트래커: 우선 재시도]")
            print(f"   [OpenRouter Error / {current_model}] 오류: {error_msg}")
            print(f"   > {wait_time:.1f}초 대기 후 다음 모델로 속개... (시도 {attempt+1}/{max_retries})\n")
            
            time.sleep(wait_time)
            continue
            
        except Exception as e:
            print(f"❌ 예상치 못한 에러: {e}")
            time.sleep(5)
            continue

    print("🚨 최대 재시도 횟수를 초과했습니다. 데이터 전송에 실패했습니다.")
    return None