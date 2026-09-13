import os
import time
import random
import requests
import re
from dotenv import load_dotenv

from openrouter_budget import (
    OpenRouterBudget,
    OpenRouterBudgetError,
    OpenRouterRequestBlocked,
    is_free_model,
)

load_dotenv()

class DummyResponse:
    """기존 파이썬 Gemini SDK의 response.text 프로퍼티와 호환성을 맞추기 위한 클래스"""
    def __init__(self, text, *, finish_reason=None):
        self.text = text
        self.finish_reason = finish_reason

def safe_generate_content(
    prompt_text,
    max_retries=10,
    *,
    model_name=None,
    backup_model_name=None,
    response_format=None,
    provider_preferences=None,
    request_timeout=120,
    request_budget=None,
    max_tokens=2000,
):
    """
    OpenRouter API 브로커 (환경변수로 지정한 주/백업 모델)
    """
    # 환경 변수 및 설정
    AI_MODEL_NAME = model_name or os.getenv("OPENROUTER_MODEL_NAME", "openrouter/free")#
    BACKUP_MODEL_NAME = backup_model_name or os.getenv("OPENROUTER_BACKUP_MODEL", "openrouter/free")# google/gemini-2.5-flash-lite,nvidia/nemotron-3-super-120b-a12b:free
    OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

    if not OPENROUTER_API_KEY:
        raise ValueError("OPENROUTER_API_KEY가 환경변수에 등록되지 않았습니다.")
    if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens <= 0:
        raise ValueError("max_tokens must be a positive integer")

    request_budget = request_budget or OpenRouterBudget.from_environment()
        
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://www.hangon.co.kr",
        "X-Title": "Hangon breaking_tracker",
    }
    
    # 속보 분석용 프롬프트는 묶음 처리 요청이므로 JSON 반환을 강제함
    enforced_prompt = prompt_text + "\n\n(IMPORTANT: 응답은 반드시 마크다운 백틱(```json) 없이 순수한 JSON 리스트([...]) 텍스트로만 반환하세요. 어떠한 설명, 사고 과정(Thought), 도입부 문구도 절대 포함하지 마세요. 바로 [ 로 시작해서 ] 로 끝나야 합니다.)"

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
            "max_tokens": max_tokens,
            "temperature": 0.2
        }
        if response_format is not None:
            data["response_format"] = response_format
        if provider_preferences is not None:
            data["provider"] = provider_preferences
        
        try:
            reservation = request_budget.reserve(current_model)
            if isinstance(reservation, dict):
                print(
                    "OpenRouter free request reserved: "
                    f"used={reservation['daily_used']}/{reservation['daily_limit']} "
                    f"remaining={reservation['daily_remaining']} "
                    f"slot={reservation['slot_used']}/{reservation['slot_limit']}"
                )
            res = requests.post(url, headers=headers, json=data, timeout=request_timeout)
            status_code = getattr(res, "status_code", None)
            is_429 = str(status_code) == "429"
            response_body = getattr(res, "text", "")
            if is_429 and is_free_model(current_model):
                request_budget.record_rate_limit(
                    model_name=current_model,
                    status_code=status_code,
                    body=response_body,
                    headers=getattr(res, "headers", None),
                )
                raise OpenRouterRequestBlocked(
                    "OpenRouter rate limit blocked further free-model requests."
                )
            res.raise_for_status()

            result_json = res.json()
            if 'error' in result_json:
                error_value = result_json["error"]
                error_code = (
                    error_value.get("code")
                    if isinstance(error_value, dict)
                    else None
                )
                if str(error_code) == "429" and is_free_model(current_model):
                    request_budget.record_rate_limit(
                        model_name=current_model,
                        status_code=429,
                        body=response_body or error_value,
                        headers=getattr(res, "headers", None),
                    )
                    raise OpenRouterRequestBlocked(
                        "OpenRouter rate limit blocked further free-model requests."
                    )
                raise RuntimeError("OpenRouter API returned an error response.")
            
            if 'choices' not in result_json:
                raise Exception(f"API 응답에 'choices'가 없습니다: {result_json}")
                
            _content = result_json['choices'][0]['message'].get('content')
            if _content is None:
                raise ValueError("API 응답의 'content'가 null(None)입니다.")
            
            raw_content = _content.strip()
            
            # 사고 과정이나 마크다운이 섞여있어도 JSON만 정교하게 추출
            content_text = extract_json_payload(raw_content)
            if not content_text:
                raise ValueError("JSON 추출 결과가 비어 있습니다.")
                
            finish_reason = None
            choices = result_json.get("choices") or []
            if choices and isinstance(choices[0], dict):
                finish_reason = choices[0].get("finish_reason")
            return DummyResponse(content_text, finish_reason=finish_reason)
            
        except OpenRouterBudgetError:
            raise

        except requests.exceptions.RequestException as e:
            status_code = getattr(getattr(e, "response", None), "status_code", None)
            if str(status_code) == "429" and is_free_model(current_model):
                request_budget.record_rate_limit(
                    model_name=current_model,
                    status_code=429,
                    body=getattr(getattr(e, "response", None), "text", ""),
                    headers=getattr(getattr(e, "response", None), "headers", None),
                )
                raise OpenRouterRequestBlocked(
                    "OpenRouter rate limit blocked further free-model requests."
                )

            wait_time = random.uniform(3, 8) * (attempt + 1)
            print(f"⚠️ [속보 트래커: 우선 재시도]")
            print(
                f"   [OpenRouter Error / {current_model}] "
                f"{type(e).__name__} (status={status_code or 'unknown'})"
            )
            print(f"   > {wait_time:.1f}초 대기 후 다음 모델로 속개... (시도 {attempt+1}/{max_retries})\n")
            
            time.sleep(wait_time)
            continue
            
        except Exception as e:
            print(f"⚠️ [재시도] {current_model} 통신 실패: {type(e).__name__}")
            time.sleep(random.uniform(3, 8) * (attempt + 1))
            continue

    print("🚨 최대 재시도 횟수를 초과했습니다. 데이터 전송에 실패했습니다.")
    return None
