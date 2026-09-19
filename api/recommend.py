import json
import os
from http.server import BaseHTTPRequestHandler
from openai import OpenAI, APIError, APIConnectionError, RateLimitError

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

SYSTEM = """당신은 한국의 요리 초보자를 위한 안전 중심 냉장고 레시피 플래너다. 반드시 한국어 JSON만 반환한다.
규칙: (1) 입력한 핵심 재료를 두 레시피 모두 반드시 포함한다. 전량 소진을 약속하지 말고 실제 사용량을 쓴다. 단위 비교가 어려우면 remaining은 '계산 어려움', 수량이 없으면 '수량 미입력'으로 쓴다. (2) 소금·후추·식용유 외 모든 재료는 additional_ingredients에 세며, 레시피별 최대 2개다. 간장·밥·면·달걀은 기본 재료가 아니다. 대체재는 A 또는 B 한 항목으로 쓴다. (3) 선택 고명은 optional_garnish로만 넣고 없어도 완성 가능해야 한다. (4) 최대 5개 조리 단계로, 각 단계는 필요한 불·시간·완료 상태를 짧게 포함한다. 생고기·달걀은 충분한 가열을 명시한다. (5) 프라이팬·냄비 등 일반 조리도구만 우선 사용하고 tools에 표시한다. (6) 알레르기와 식단 제한 및 연관 식재료(우유-버터/치즈, 대두-간장 등)를 보수적으로 검토한다. 충돌하거나 불확실하면 안전하다고 단정하지 않는다. 핵심 재료와 제한이 충돌하면 status='conflict', recipes=[]로 하고 수정 방법을 message에 쓴다. (7) 식재료명이 아니거나 모호하면 status='invalid', recipes=[]로 하고 재입력을 요청한다. (8) 조건을 만족하는 간단한 한 끼 2개를 만들 수 없으면 status='invalid'로 안내한다. (9) status='ok'일 때 recipes는 정확히 2개, 각 ingredient_usage는 모든 핵심 재료를 포함한다. restriction_note에는 반영을 시도한 제한과 성분표/교차오염 직접 확인 경고를 쓴다."""

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
            payload = json.loads(self.rfile.read(size).decode('utf-8'))
            if not payload.get('ingredients') or not payload.get('servings'):
                self._send(400, {"message":"꼭 쓰고 싶은 재료와 인원수를 입력해 주세요."}); return
            client = OpenAI(api_key=os.environ['OPENAI_API_KEY'])
            user_input = json.dumps(payload, ensure_ascii=False)
            response = client.responses.create(model="gpt-5-mini", reasoning={"effort":"low"}, max_output_tokens=1800, input=[{"role":"system","content":SYSTEM},{"role":"user","content":f"다음 사용자 입력으로 추천해줘: {user_input}"}], text={"format":{"type":"json_schema","name":"quick_recipe_recommendation","strict":True,"schema":SCHEMA}})
            result = json.loads(response.output_text)
            if not is_valid_result(result):
                self._send(502, {"message":"추천 결과가 기준을 충족하지 못했어요. 다시 시도해 주세요."}); return
            self._send(200, result)
        except (json.JSONDecodeError, ValueError): self._send(400, {"message":"요청 형식을 읽지 못했어요. 다시 시도해 주세요."})
        except RateLimitError: self._send(429, {"message":"추천 요청이 많아요. 잠시 후 다시 시도해 주세요."})
        except (APIConnectionError, APIError): self._send(502, {"message":"AI 추천 서비스와 연결하지 못했어요. 잠시 후 다시 시도해 주세요."})
        except Exception: self._send(500, {"message":"추천을 만드는 중 서버 오류가 발생했어요. 잠시 후 다시 시도해 주세요."})
