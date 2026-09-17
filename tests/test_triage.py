from __future__ import annotations

import sqlite3

from fastapi.testclient import TestClient

from app.config import Settings
from app.llm import LLMError, apply_rules, extract_json_object, operator_fallback, text_is_thin
from app.main import create_app
from app.schemas import OPERATOR_REPLY, TriageIn, TriageOut


def _settings(tmp_path, **kwargs) -> Settings:
    values = {
        "openai_api_key": "test-key",
        "sqlite_path": str(tmp_path / "tickets.db"),
        "rate_limit_per_minute": 3,
        "seed_demo": False,
    }
    values.update(kwargs)
    return Settings(**values)


def _ok_classifier(_payload: TriageIn) -> TriageOut:
    return TriageOut(
        category="billing",
        draft_reply="Мы получили вопрос по оплате и проверим его по данным из обращения.",
        confidence="high",
        escalate=False,
    )


def test_health(tmp_path):
    client = TestClient(create_app(_settings(tmp_path), classify_fn=_ok_classifier))
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_triage_contract(tmp_path):
    client = TestClient(create_app(_settings(tmp_path), classify_fn=_ok_classifier))
    response = client.post(
        "/triage",
        json={
            "text": "С карты списали оплату за заказ, но статус в кабинете всё ещё «не оплачен».",
            "channel": "email",
            "client_id": "c1",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"category", "draft_reply", "confidence", "escalate"}
    assert body["category"] == "billing"
    assert body["confidence"] == "high"
    assert body["escalate"] is False


def test_validation_empty_text(tmp_path):
    client = TestClient(create_app(_settings(tmp_path), classify_fn=_ok_classifier))
    response = client.post(
        "/triage",
        json={"text": "   ", "channel": "form", "client_id": "c1"},
    )
    assert response.status_code == 422


def test_validation_too_long(tmp_path):
    client = TestClient(create_app(_settings(tmp_path), classify_fn=_ok_classifier))
    response = client.post(
        "/triage",
        json={"text": "a" * 2001, "channel": "chat", "client_id": "c1"},
    )
    assert response.status_code == 422


def test_rate_limit(tmp_path):
    client = TestClient(create_app(_settings(tmp_path, rate_limit_per_minute=2), classify_fn=_ok_classifier))
    payload = {
        "text": "Не приходит чек об оплате заказа, хотя деньги списались.",
        "channel": "form",
        "client_id": "same-client",
    }
    assert client.post("/triage", json=payload).status_code == 200
    assert client.post("/triage", json=payload).status_code == 200
    blocked = client.post("/triage", json=payload)
    assert blocked.status_code == 429


def test_llm_failure_fallback(tmp_path):
    def boom(_payload: TriageIn) -> TriageOut:
        raise LLMError("model returned non-json")

    settings = _settings(tmp_path)
    app = create_app(settings, classify_fn=boom)
    client = TestClient(app)
    response = client.post(
        "/triage",
        json={
            "text": "Товар пришёл разбитым, упаковка была порвана, прошу заменить.",
            "channel": "email",
            "client_id": "c2",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["escalate"] is True
    assert body["confidence"] == "low"
    assert body["draft_reply"] == OPERATOR_REPLY
    assert body["category"] == "other"

    conn = sqlite3.connect(settings.sqlite_path)
    row = conn.execute("SELECT error, escalate, draft_reply, text FROM tickets").fetchone()
    conn.close()
    assert row is not None
    assert "non-json" in row[0]
    assert row[1] == 1
    assert row[2] == OPERATOR_REPLY
    assert "разбитым" in row[3]


def test_history_keeps_request_and_draft(tmp_path):
    settings = _settings(tmp_path)
    client = TestClient(create_app(settings, classify_fn=_ok_classifier))
    text = "С карты списали оплату за заказ, но статус в кабинете всё ещё «не оплачен»."
    client.post(
        "/triage",
        json={
            "text": text,
            "channel": "email",
            "client_id": "anna@shop.test",
            "client_name": "Анна Котова",
            "client_email": "anna@shop.test",
            "client_phone": "+79001112233",
            "order_number": "45812",
        },
    )
    history = client.get("/tickets")
    assert history.status_code == 200
    items = history.json()
    assert len(items) == 1
    assert items[0]["text"] == text
    assert items[0]["client_name"] == "Анна Котова"
    assert items[0]["order_number"] == "45812"
    assert "оплате" in items[0]["draft_reply"]

    people = client.get("/clients").json()
    assert len(people) == 1
    assert people[0]["email"] == "anna@shop.test"
    assert people[0]["name"] == "Анна Котова"


def test_partial_client_data_links_existing_person(tmp_path):
    settings = _settings(tmp_path)
    client = TestClient(create_app(settings, classify_fn=_ok_classifier))
    first = {
        "text": "С карты списали оплату за заказ, чек не пришёл, статус в кабинете не обновился.",
        "channel": "email",
        "client_id": "anna@shop.test",
        "client_name": "Анна Котова",
        "client_email": "anna@shop.test",
        "client_phone": "+7 900 111-22-33",
        "order_number": "45812",
    }
    assert client.post("/triage", json=first).status_code == 200
    second = {
        "text": "Всё ещё нет кода получения в пункте выдачи, заказ уже там.",
        "channel": "chat",
        "client_id": "temp",
        "client_phone": "79001112233",
    }
    assert client.post("/triage", json=second).status_code == 200
    items = client.get("/tickets").json()
    latest = items[0]
    assert latest["client_name"] == "Анна Котова"
    assert latest["client_email"] == "anna@shop.test"
    assert latest["client_id"] == "anna@shop.test"
    assert client.get("/clients").json()[0]["name"] == "Анна Котова"


def test_name_lookup_fills_contacts(tmp_path):
    settings = _settings(tmp_path)
    client = TestClient(create_app(settings, classify_fn=_ok_classifier))
    first = {
        "text": "С карты списали оплату за заказ, чек не пришёл, статус в кабинете не обновился.",
        "channel": "email",
        "client_id": "anna@shop.test",
        "client_name": "Анна Котова",
        "client_email": "anna@shop.test",
        "client_phone": "+7 900 111-22-33",
        "order_number": "45812",
    }
    assert client.post("/triage", json=first).status_code == 200
    second = {
        "text": "В пункте выдачи нет кода получения, посылка уже на месте.",
        "channel": "form",
        "client_id": "Анна Котова",
        "client_name": "анна  котова",
    }
    assert client.post("/triage", json=second).status_code == 200
    latest = client.get("/tickets").json()[0]
    assert latest["client_email"] == "anna@shop.test"
    assert latest["client_phone"] == "+7 900 111-22-33"
    assert latest["order_number"] == "45812"
    assert latest["client_id"] == "anna@shop.test"


def test_thin_text_forces_escalation():
    result = apply_rules("Помогите", {
        "category": "other",
        "draft_reply": "Уточните, пожалуйста, номер заказа и суть вопроса.",
        "confidence": "high",
        "escalate": False,
    })
    assert text_is_thin("Помогите")
    assert result.confidence == "low"
    assert result.escalate is True


def test_extract_json_from_fences():
    data = extract_json_object('```json\n{"category":"support","draft_reply":"ok.","confidence":"medium","escalate":false}\n```')
    assert data["category"] == "support"


def test_missing_api_key_prevents_startup(tmp_path):
    app = create_app(_settings(tmp_path, openai_api_key="", proxy_api_key=""))
    try:
        with TestClient(app):
            raise AssertionError("startup should fail without API key")
    except RuntimeError as exc:
        assert "OPENAI_API_KEY" in str(exc)


def test_demo_clients_are_seeded(tmp_path):
    client = TestClient(create_app(_settings(tmp_path, seed_demo=True), classify_fn=_ok_classifier))
    people = client.get("/clients").json()
    names = {item["name"] for item in people}
    assert "Анна Котова" in names
    assert "Иван Петров" in names
    anna = next(item for item in people if item["name"] == "Анна Котова")
    assert anna["email"] is None
    assert "900" in (anna["phone"] or "")
