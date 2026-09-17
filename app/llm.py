from __future__ import annotations

import json
import re

from openai import OpenAI

from app.config import Settings
from app.schemas import (
    CATEGORIES,
    CONFIDENCE_LEVELS,
    MIN_TEXT_CHARS,
    MIN_TEXT_WORDS,
    OPERATOR_REPLY,
    TriageIn,
    TriageOut,
)

SYSTEM_PROMPT = """Ты старший специалист поддержки интернет-магазина.
Верни ТОЛЬКО JSON без markdown.

Поля:
- category: billing | support | complaint | other
- draft_reply: 2–5 предложений, готовый черновик письма клиенту
- confidence: high | medium | low
- escalate: true | false

Зачем нужен черновик: менеджер не пишет письмо с нуля. Он читает, правит детали и отправляет.

Как писать draft_reply:
1. Обратись по имени, если имя есть.
2. Коротко повтори суть проблемы словами клиента (оплата не прошла, нет кода ПВЗ, товар повреждён и т.д.).
3. Напиши конкретное следующее действие магазина по этой сути:
   - billing: проверим списание и статус заказа, вышлем чек или разберём двойное списание;
   - support: подскажем шаг (код выдачи, отслеживание, кабинет) и что сделаем мы;
   - complaint: извинись по факту претензии и скажи, что передаём в доставку/качество для разбора.
4. Если есть номер заказа — упомяни его. Если имени/номера нет — всё равно разбери тему, не отказывай в помощи.
5. Нельзя делать черновик из фраз «не могу помочь», «уточните данные», «опишите подробнее» как основного ответа.
6. Не выдумывай суммы, даты, статусы, трек-номера и сроки возврата, которых нет во входе.
7. Номер заказа проси только в последнем предложении и только если его реально нет. Это дополнение, не замена ответа.

Категории:
- billing: оплата, чек, списание, возврат денег;
- support: заказ, доставка, код выдачи, кабинет, как сделать действие;
- complaint: претензия к качеству, курьеру, срокам, тону;
- other: нельзя понять тему.

confidence:
- high — тема ясна;
- medium — тема ясна, не хватает мелочи (номер заказа);
- low — текст бессодержательный, вроде «помогите».

escalate=true только если тема неясна, это спор/претензия с риском или текст почти пустой. Не эскалируй только из-за отсутствия номера заказа.

Язык draft_reply — язык обращения.
"""


class LLMError(Exception):
    """LLM call or JSON contract failed."""


def operator_fallback() -> TriageOut:
    return TriageOut(
        category="other",
        draft_reply=OPERATOR_REPLY,
        confidence="low",
        escalate=True,
    )


def text_is_thin(text: str) -> bool:
    words = [part for part in re.split(r"\s+", text.strip()) if part]
    return len(text.strip()) < MIN_TEXT_CHARS or len(words) < MIN_TEXT_WORDS


def clip_sentences(text: str, max_n: int = 6) -> str:
    cleaned = " ".join(text.split())
    parts = [p.strip() for p in re.split(r"(?<=[.!?…])\s+", cleaned) if p.strip()]
    if len(parts) <= 1:
        return cleaned
    if len(parts) > max_n:
        return " ".join(parts[:max_n])
    return cleaned


def extract_json_object(raw: str) -> dict:
    content = raw.strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*", "", content)
        content = re.sub(r"\s*```$", "", content)
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        start = content.find("{")
        end = content.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise LLMError("model returned non-json")
        data = json.loads(content[start : end + 1])
    if not isinstance(data, dict):
        raise LLMError("model json is not an object")
    return data


def apply_rules(text: str, data: dict) -> TriageOut:
    category = data.get("category")
    confidence = data.get("confidence")
    draft = data.get("draft_reply")
    escalate = data.get("escalate")

    if category not in CATEGORIES:
        raise LLMError("invalid category")
    if confidence not in CONFIDENCE_LEVELS:
        raise LLMError("invalid confidence")
    if not isinstance(draft, str) or not draft.strip():
        raise LLMError("empty draft_reply")
    if not isinstance(escalate, bool):
        raise LLMError("invalid escalate")

    draft_reply = clip_sentences(draft.strip())
    if text_is_thin(text):
        confidence = "low"
        escalate = True
    elif confidence == "low":
        escalate = True

    return TriageOut(
        category=category,
        draft_reply=draft_reply,
        confidence=confidence,
        escalate=escalate,
    )


def _client_block(payload: TriageIn) -> str:
    parts = [
        f"channel: {payload.channel}",
        f"client_id: {payload.client_id}",
        f"имя: {payload.client_name or 'не указано'}",
        f"email: {payload.client_email or 'не указан'}",
        f"телефон: {payload.client_phone or 'не указан'}",
        f"номер заказа: {payload.order_number or 'не указан'}",
        "текст обращения:",
        payload.text,
    ]
    return "\n".join(parts)


def classify_with_llm(settings: Settings, payload: TriageIn) -> TriageOut:
    client = OpenAI(
        api_key=settings.resolved_api_key(),
        base_url=settings.openai_base_url,
        timeout=settings.llm_timeout_seconds,
    )
    try:
        response = client.chat.completions.create(
            model=settings.openai_model,
            temperature=0.2,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": _client_block(payload)},
            ],
        )
    except Exception as exc:
        raise LLMError(f"llm request failed: {exc}") from exc

    try:
        content = response.choices[0].message.content or ""
    except (IndexError, AttributeError) as exc:
        raise LLMError("llm response missing content") from exc

    parsed = extract_json_object(content)
    return apply_rules(payload.text, parsed)


def redact_error(message: str, api_key: str) -> str:
    cleaned = message
    if api_key:
        cleaned = cleaned.replace(api_key, "***")
    return cleaned[:500]
