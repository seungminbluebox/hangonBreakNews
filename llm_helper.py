import os
import time
import random
import json
import requests
import re
from dotenv import load_dotenv

from openrouter_budget import (
    OpenRouterBudget,
    OpenRouterBudgetError,
    OpenRouterRequestBlocked,
    is_free_model,
)
from cycle_logging import current_context, log_event, record_ai_call, record_retry

load_dotenv()

_BLOCKED_NOTICE_KEYS = set()
AI_FAILURE_REASONS = frozenset({
    "timeout", "network_error", "http_error", "empty_response",
    "invalid_json", "output_truncated", "schema_error", "unexpected_error",
})


class AIRequestError(RuntimeError):
    """Safe, typed AI failure without retaining provider response details."""

    def __init__(self, reason, status_code=None):
        self.reason = reason if reason in AI_FAILURE_REASONS else "unexpected_error"
        self.status_code = status_code if isinstance(status_code, int) else None
        super().__init__(self.reason)


class _FailureSignal(Exception):
    def __init__(self, reason, status_code=None):
        self.reason = reason
        self.status_code = status_code

class DummyResponse:
    """기존 파이썬 Gemini SDK의 response.text 프로퍼티와 호환성을 맞추기 위한 클래스"""
    def __init__(self, text, *, finish_reason=None):
        self.text = text
        self.finish_reason = finish_reason


def _log_rate_limit_transition(transition, request_budget=None):
    if isinstance(transition, dict):
        changed = transition.get("changed")
        fields = {
            "reason": transition.get("reason"),
            "blocked_until": transition.get("blocked_until"),
        }
    else:
        changed = bool(transition)
        fields = {}
    if changed:
        key = (getattr(request_budget, "path", None), fields.get("reason"), fields.get("blocked_until"))
        if key not in _BLOCKED_NOTICE_KEYS:
            _BLOCKED_NOTICE_KEYS.add(key)
            log_event("rate_limit_transition", level=30, status="blocked", **fields)


def _log_persisted_block(request_budget):
    try:
        snapshot = request_budget.snapshot()
    except Exception:
        return
    reason = snapshot.get("blocked_reason")
    blocked_until = snapshot.get("blocked_until")
    if reason is None or blocked_until is None:
        return
    key = (getattr(request_budget, "path", None), reason, blocked_until)
    if key not in _BLOCKED_NOTICE_KEYS:
        _BLOCKED_NOTICE_KEYS.add(key)
        log_event(
            "rate_limit_transition",
            level=30,
            status="blocked",
            reason=reason,
            blocked_until=blocked_until,
        )


def _clear_blocked_notices(request_budget):
    path = getattr(request_budget, "path", None)
    stale_keys = [key for key in _BLOCKED_NOTICE_KEYS if key[0] == path]
    _BLOCKED_NOTICE_KEYS.difference_update(stale_keys)

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
    raise_on_failure=False,
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

    last_failure = ("unexpected_error", None)

    def retry_or_raise(reason, status_code, attempt):
        nonlocal last_failure
        last_failure = (reason, status_code)
        if attempt < max_retries - 1:
            record_retry()
            if current_context() is None:
                log_event(
                    "ai_retry",
                    level=30,
                    attempt=attempt + 1,
                    max_attempts=max_retries,
                    reason=reason,
                    status_code=status_code,
                )
            time.sleep(random.uniform(3, 8) * (attempt + 1))
            return True
        if raise_on_failure:
            raise AIRequestError(reason, status_code)
        return False

    for attempt in range(max_retries):
        # 첫 2회까지는 메인 모델, 그 이후는 백업 모델 시도
        current_model = AI_MODEL_NAME if attempt < 2 else BACKUP_MODEL_NAME
        status_code = None

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
                context = current_context()
                if context is not None:
                    context.set_budget(
                        used=reservation.get("daily_used"),
                        limit=reservation.get("daily_limit"),
                    )
                log_event(
                    "ai_request_reserved",
                    level=10,
                    used=reservation.get("daily_used"),
                    remaining=reservation.get("daily_remaining"),
                    slot_used=reservation.get("slot_used"),
                    slot_limit=reservation.get("slot_limit"),
                )
                if reservation.get("breaker_released"):
                    _clear_blocked_notices(request_budget)
                    log_event("rate_limit_transition", level=20, status="unblocked")
            record_ai_call()
            res = requests.post(url, headers=headers, json=data, timeout=request_timeout)
            status_code = getattr(res, "status_code", None)
            is_429 = str(status_code) == "429"
            response_body = getattr(res, "text", "")
            if is_429 and is_free_model(current_model):
                transition = request_budget.record_rate_limit(
                    model_name=current_model,
                    status_code=status_code,
                    body=response_body,
                    headers=getattr(res, "headers", None),
                )
                _log_rate_limit_transition(transition, request_budget)
                raise OpenRouterRequestBlocked(
                    "OpenRouter rate limit blocked further free-model requests."
                )
            res.raise_for_status()
            if isinstance(status_code, int) and status_code >= 400:
                raise _FailureSignal("http_error", status_code)

            try:
                result_json = res.json()
            except (requests.exceptions.JSONDecodeError, json.JSONDecodeError, ValueError):
                raise _FailureSignal("invalid_json", status_code)
            if not isinstance(result_json, dict):
                raise _FailureSignal("schema_error", status_code)
            if "error" in result_json:
                error_value = result_json["error"]
                error_code = (
                    error_value.get("code")
                    if isinstance(error_value, dict)
                    else None
                )
                if str(error_code) == "429" and is_free_model(current_model):
                    transition = request_budget.record_rate_limit(
                        model_name=current_model,
                        status_code=429,
                        body=response_body or error_value,
                        headers=getattr(res, "headers", None),
                    )
                    _log_rate_limit_transition(transition, request_budget)
                    raise OpenRouterRequestBlocked(
                        "OpenRouter rate limit blocked further free-model requests."
                    )
                safe_code = error_code if isinstance(error_code, int) else None
                raise _FailureSignal("http_error", safe_code)

            choices = result_json.get("choices")
            if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
                raise _FailureSignal("schema_error", status_code)
            choice = choices[0]
            finish_reason = choice.get("finish_reason")
            if finish_reason == "length":
                raise _FailureSignal("output_truncated", status_code)
            message = choice.get("message")
            if not isinstance(message, dict):
                raise _FailureSignal("schema_error", status_code)
            _content = message.get("content")
            if _content is None:
                raise _FailureSignal("empty_response", status_code)
            if not isinstance(_content, str):
                raise _FailureSignal("schema_error", status_code)

            raw_content = _content.strip()
            # 사고 과정이나 마크다운이 섞여있어도 JSON만 정교하게 추출
            content_text = extract_json_payload(raw_content)
            if not content_text:
                raise _FailureSignal("empty_response", status_code)
            return DummyResponse(content_text, finish_reason=finish_reason)
            
        except OpenRouterRequestBlocked:
            _log_persisted_block(request_budget)
            raise
        except OpenRouterBudgetError:
            raise

        except _FailureSignal as failure:
            if retry_or_raise(failure.reason, failure.status_code, attempt):
                continue

        except requests.exceptions.JSONDecodeError:
            if retry_or_raise("invalid_json", status_code, attempt):
                continue

        except requests.exceptions.Timeout:
            if retry_or_raise("timeout", status_code, attempt):
                continue

        except requests.exceptions.HTTPError as error:
            error_status = getattr(getattr(error, "response", None), "status_code", None)
            if str(error_status) == "429" and is_free_model(current_model):
                transition = request_budget.record_rate_limit(
                    model_name=current_model,
                    status_code=429,
                    body=getattr(getattr(error, "response", None), "text", ""),
                    headers=getattr(getattr(error, "response", None), "headers", None),
                )
                _log_rate_limit_transition(transition, request_budget)
                raise OpenRouterRequestBlocked(
                    "OpenRouter rate limit blocked further free-model requests."
                )
            if retry_or_raise("http_error", error_status, attempt):
                continue

        except requests.exceptions.ConnectionError:
            if retry_or_raise("network_error", status_code, attempt):
                continue

        except requests.exceptions.RequestException as error:
            error_status = getattr(getattr(error, "response", None), "status_code", None)
            if str(error_status) == "429" and is_free_model(current_model):
                transition = request_budget.record_rate_limit(
                    model_name=current_model,
                    status_code=429,
                    body=getattr(getattr(error, "response", None), "text", ""),
                    headers=getattr(getattr(error, "response", None), "headers", None),
                )
                _log_rate_limit_transition(transition, request_budget)
                raise OpenRouterRequestBlocked(
                    "OpenRouter rate limit blocked further free-model requests."
                )
            reason = "http_error" if isinstance(error_status, int) and error_status >= 400 else "network_error"
            if retry_or_raise(reason, error_status, attempt):
                continue

        except Exception:
            reason = "http_error" if isinstance(status_code, int) and status_code >= 400 else "unexpected_error"
            if retry_or_raise(reason, status_code, attempt):
                continue

    if current_context() is None:
        log_event("ai_failed", level=40, status="failed", reason=last_failure[0], status_code=last_failure[1])
    if raise_on_failure:
        raise AIRequestError(*last_failure)
    return None
