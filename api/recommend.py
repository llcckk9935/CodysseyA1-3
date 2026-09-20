import json
import os
import re
import uuid
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse
from openai import OpenAI, APIError, APIConnectionError, APIStatusError, RateLimitError

SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "status": {"type": "string", "enum": ["ok", "invalid", "conflict"]},
        "message": {"type": "string"},
        "recognized_ingredients": {"type": "array", "items": {"type": "string"}},
        "overall_note": {"type": "string"},
        "recipes": {"type": "array", "items": {"type": "object", "additionalProperties": False, "properties": {
            "id": {"type": "string"}, "name": {"type": "string"}, "reason": {"type": "string"}, "time": {"type": "string"}, "difficulty": {"type": "string"},
            "tools": {"type": "array", "items": {"type": "string"}}, "restriction_note": {"type": "string"},
            "ingredient_usage": {"type": "array", "items": {"type": "object", "additionalProperties": False, "properties": {"ingredient": {"type": "string"}, "amount": {"type": "string"}, "remaining": {"type": "string"}}, "required": ["ingredient", "amount", "remaining"]}},
            "additional_ingredients": {"type": "array", "items": {"type": "string"}}, "optional_garnish": {"type": "string"}, "steps": {"type": "array", "items": {"type": "string"}}
        }, "required": ["id", "name", "reason", "time", "difficulty", "tools", "restriction_note", "ingredient_usage", "additional_ingredients", "optional_garnish", "steps"]}},
    }, "required": ["status", "message", "recognized_ingredients", "overall_note", "recipes"]
}

SYSTEM = """당신은 한국의 요리 초보자를 위한 안전 중심 냉장고 레시피 플래너다. JSON이나 마크다운 코드 블록을 쓰지 말고 아래의 한국어 라벨 형식을 정확히 따른다.
규칙: 입력한 핵심 재료를 두 레시피 모두 반드시 포함한다. 소금·후추·식용유 외 모든 재료는 추가 재료이며 레시피별 최대 2개다. 간장·밥·면·달걀은 기본 재료가 아니다. 조리 단계는 4개, 각 단계는 짧고 불·시간·완료 상태를 포함한다. 알레르기·식단 제한 및 우유-버터/치즈, 대두-간장 같은 연관 재료를 보수적으로 피한다. 생고기·달걀은 충분한 가열을 쓴다.
정상일 때는 다음 형식의 레시피 2개만 출력한다. 모든 라벨을 빠뜨리지 않는다.
[레시피 1]
메뉴명: 짧은 메뉴명
추천 이유: 짧은 이유
시간: 15분
난이도: 쉬움
도구: 프라이팬, 냄비
제한 안내: 반영한 제한과 성분표·교차오염 확인 안내
핵심 재료 사용량: 재료 | 사용량 | 예상 잔량; 재료 | 사용량 | 예상 잔량
추가 재료: 재료 또는 없음
선택 재료: 재료 또는 없음
조리 순서:
1. 첫 단계
2. 둘째 단계
3. 셋째 단계
4. 넷째 단계
[레시피 2]
(위와 같은 라벨과 형식)
핵심 재료와 제한이 충돌하거나 식재료명이 모호하면 레시피 대신 아래 형식만 출력한다.
[오류]
유형: conflict 또는 invalid
메시지: 사용자가 수정할 방법"""

def label_value(block, label, default=""):
    match = re.search(rf"^\s*{re.escape(label)}:\s*(.+?)\s*$", block, re.MULTILINE)
    return match.group(1).strip() if match else default

def split_items(value):
    if not value or value in {"없음", "해당 없음"}:
        return []
    return [item.strip() for item in re.split(r"[,，]", value) if item.strip()]

def response_text(message):
    """Read text from both standard and OpenAI-compatible gateway messages."""
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("content") or ""))
            else:
                parts.append(str(getattr(item, "text", "") or getattr(item, "content", "")))
        return "\n".join(part for part in parts if part).strip()

    # Some education gateways expose the generated text in this extension
    # instead of the standard Chat Completions content field.
    alternative = getattr(message, "reasoning_content", None)
    if isinstance(alternative, str) and "[레시피" in alternative:
        return alternative.strip()
    return ""

def parse_labelled_result(raw_output, payload):
    error_type = label_value(raw_output, "유형")
    if "[오류]" in raw_output and error_type in {"conflict", "invalid"}:
        return {"status": error_type, "message": label_value(raw_output, "메시지", "입력 정보를 수정해 주세요."), "recognized_ingredients": [], "overall_note": "", "recipes": []}
    blocks = re.split(r"\[레시피\s*[12]\]", raw_output)
    recipe_blocks = [block for block in blocks[1:] if block.strip()]
    if len(recipe_blocks) != 2:
        return None
    recipes = []
    for block in recipe_blocks:
        usage = []
        for item in label_value(block, "핵심 재료 사용량").split(";"):
            parts = [part.strip() for part in item.split("|")]
            if len(parts) >= 2 and parts[0]:
                usage.append({"ingredient": parts[0], "amount": parts[1], "remaining": parts[2] if len(parts) >= 3 else "계산 어려움"})
        steps = re.findall(r"^\s*\d+[.)]\s*(.+?)\s*$", block, re.MULTILINE)
        recipes.append({
            "id": str(uuid.uuid4()), "name": label_value(block, "메뉴명"), "reason": label_value(block, "추천 이유"),
            "time": label_value(block, "시간"), "difficulty": label_value(block, "난이도"),
            "tools": split_items(label_value(block, "도구")), "restriction_note": label_value(block, "제한 안내"),
            "ingredient_usage": usage, "additional_ingredients": split_items(label_value(block, "추가 재료")),
            "optional_garnish": label_value(block, "선택 재료"), "steps": steps,
        })
    recognized = [item.strip() for item in re.split(r"[,，]", payload.get("ingredients", "")) if item.strip()]
    return {"status": "ok", "message": "", "recognized_ingredients": recognized, "overall_note": "입력한 핵심 재료를 모두 포함하도록 추천했어요.", "recipes": recipes}

def is_valid_result(result):
    """Prevent an incomplete structured response from reaching the browser."""
    if result.get("status") != "ok":
        return result.get("status") in {"invalid", "conflict"}
    recipes = result.get("recipes", [])
    if len(recipes) != 2 or not result.get("recognized_ingredients"):
        return False
    for recipe in recipes:
        if len(recipe.get("additional_ingredients", [])) > 2:
            return False
        if not 1 <= len(recipe.get("steps", [])) <= 5:
            return False
        if not recipe.get("ingredient_usage"):
            return False
    return True

class handler(BaseHTTPRequestHandler):
    STATIC_FILES = {
        "/": ("index.html", "text/html; charset=utf-8"),
        "/index.html": ("index.html", "text/html; charset=utf-8"),
        "/css/style.css": ("css/style.css", "text/css; charset=utf-8"),
        "/js/app.js": ("js/app.js", "application/javascript; charset=utf-8"),
    }

    def do_GET(self):
        """Serve the vanilla frontend when this Python entrypoint owns the root route."""
        path = urlparse(self.path).path
        asset = self.STATIC_FILES.get(path)
        if not asset:
            self._send(404, {"message": "요청한 페이지를 찾을 수 없어요."})
            return
        file_path = Path(__file__).resolve().parent.parent / asset[0]
        try:
            body = file_path.read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', asset[1])
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except OSError:
            self._send(500, {"message": "화면 파일을 불러오지 못했어요."})

    def _send(self, status, data):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(status); self.send_header('Content-Type', 'application/json; charset=utf-8'); self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
    def do_OPTIONS(self):
        self.send_response(204); self.send_header('Allow', 'POST, OPTIONS'); self.end_headers()
    def do_POST(self):
        if not os.getenv('OPENAI_API_KEY'):
            self._send(500, {"message":"서버의 AI 설정이 완료되지 않았어요. 관리자에게 문의해 주세요."}); return
        try:
            size = int(self.headers.get('Content-Length', 0))
            if size > 12000: self._send(400, {"message":"입력 내용이 너무 길어요. 재료만 간단히 입력해 주세요."}); return
            try:
                payload = json.loads(self.rfile.read(size).decode('utf-8'))
            except json.JSONDecodeError:
                self._send(400, {"message":"입력 정보를 읽지 못했어요. 다시 입력해 주세요."}); return
            if not payload.get('ingredients') or not payload.get('servings'):
                self._send(400, {"message":"꼭 쓰고 싶은 재료와 인원수를 입력해 주세요."}); return
            # Optional for the education-provider gateway. If absent, the official OpenAI endpoint is used.
            client_options = {"api_key": os.environ['OPENAI_API_KEY']}
            if os.getenv('OPENAI_BASE_URL'):
                client_options["base_url"] = os.environ['OPENAI_BASE_URL']
            # Finish before the browser's 45-second UX timeout.
            client_options["timeout"] = 42.0
            client_options["max_retries"] = 0
            client = OpenAI(**client_options)
            user_input = json.dumps(payload, ensure_ascii=False)
            request_options = dict(
                model="gpt-5-mini",
                # GPT-5 uses part of this budget for reasoning. Low effort leaves
                # enough tokens for the two recipe cards on supported gateways.
                max_tokens=3000,
                reasoning_effort="low",
                messages=[
                    {"role": "system", "content": SYSTEM},
                    {"role": "user", "content": f"다음 사용자 입력으로 추천해줘: {user_input}"},
                ],
            )
            try:
                response = client.chat.completions.create(**request_options)
            except APIStatusError as error:
                # Several OpenAI-compatible education gateways reject GPT-5's
                # optional reasoning parameter but accept the same chat request.
                if error.status_code != 400:
                    raise
                request_options.pop("reasoning_effort")
                response = client.chat.completions.create(**request_options)
            raw_output = response_text(response.choices[0].message)
            result = parse_labelled_result(raw_output, payload)
            if not result:
                if raw_output.strip():
                    self._send(200, {"status": "raw", "message": "", "recognized_ingredients": [item.strip() for item in re.split(r"[,，]", payload["ingredients"]) if item.strip()], "overall_note": "AI가 제안한 원문 레시피예요. 성분표와 알레르기 정보를 직접 확인해 주세요.", "raw_text": raw_output}); return
                self._send(502, {"message":"AI가 빈 응답을 반환했어요. 잠시 후 다시 시도해 주세요."}); return
            if not is_valid_result(result):
                self._send(200, {"status": "raw", "message": "", "recognized_ingredients": [item.strip() for item in re.split(r"[,，]", payload["ingredients"]) if item.strip()], "overall_note": "AI가 제안한 원문 레시피예요. 성분표와 알레르기 정보를 직접 확인해 주세요.", "raw_text": raw_output}); return
            self._send(200, result)
        except ValueError: self._send(400, {"message":"요청 형식을 읽지 못했어요. 다시 시도해 주세요."})
        except RateLimitError: self._send(429, {"message":"추천 요청이 많아요. 잠시 후 다시 시도해 주세요."})
        except APIStatusError as error:
            messages = {
                400: "AI 제공사가 요청 형식을 처리하지 못했어요. 잠시 후 다시 시도해 주세요.",
                401: "AI 인증에 실패했어요. Vercel의 API 키와 교육장 Base URL 설정을 확인해 주세요.",
                403: "현재 교육용 API 키에 이 모델 사용 권한이 없어요. 교육장 안내의 지원 모델을 확인해 주세요.",
                404: "교육장 API에서 요청한 모델 또는 Responses API 경로를 찾지 못했어요. 지원 모델을 확인해 주세요.",
            }
            self._send(error.status_code or 502, {"message": messages.get(error.status_code, "AI 추천 서비스가 요청을 처리하지 못했어요. 잠시 후 다시 시도해 주세요.")})
        except (APIConnectionError, APIError): self._send(502, {"message":"AI 추천 서비스와 연결하지 못했어요. 잠시 후 다시 시도해 주세요."})
        except Exception: self._send(500, {"message":"추천을 만드는 중 서버 오류가 발생했어요. 잠시 후 다시 시도해 주세요."})
