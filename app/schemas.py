from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

TRIAGE_EXAMPLE = {
    "text": "С карты списали оплату за заказ 45812, чек не пришёл, в кабинете статус «ожидает оплаты».",
    "channel": "email",
    "client_id": "anna@shop.test",
    "client_name": "Анна Котова",
    "client_email": "anna@shop.test",
    "client_phone": "+7 900 111-22-33",
    "order_number": "45812",
}

Category = Literal["billing", "support", "complaint", "other"]
Confidence = Literal["high", "medium", "low"]
CATEGORIES: tuple[str, ...] = ("billing", "support", "complaint", "other")
CONFIDENCE_LEVELS: tuple[str, ...] = ("high", "medium", "low")

OPERATOR_REPLY = "Ваше обращение передано оператору."
MIN_TEXT_CHARS = 24
MIN_TEXT_WORDS = 4
CATEGORY_LABELS = {
    "billing": "Оплата",
    "support": "Поддержка",
    "complaint": "Жалоба",
    "other": "Другое",
}


class TriageIn(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [TRIAGE_EXAMPLE]})

    text: str = Field(
        min_length=1,
        max_length=2000,
        examples=["С карты списали оплату за заказ 45812, чек не пришёл."],
    )
    channel: str = Field(min_length=1, max_length=32, examples=["email"])
    client_id: str = Field(min_length=1, max_length=128, examples=["anna@shop.test"])
    client_name: str | None = Field(default=None, max_length=120)
    client_email: str | None = Field(default=None, max_length=120)
    client_phone: str | None = Field(default=None, max_length=32)
    order_number: str | None = Field(default=None, max_length=64)

    @field_validator("text", "channel", "client_id")
    @classmethod
    def strip_not_empty(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("must not be empty")
        return cleaned

    @field_validator("client_name", "client_email", "client_phone", "order_number", mode="before")
    @classmethod
    def empty_to_none(cls, value: object) -> str | None:
        if value is None:
            return None
        cleaned = str(value).strip()
        return cleaned or None


class TriageOut(BaseModel):
    category: Category
    draft_reply: str = Field(min_length=1, max_length=2000)
    confidence: Confidence
    escalate: bool


class TicketRecord(BaseModel):
    id: int
    created_at: str
    client_id: str
    channel: str
    client_name: str | None = None
    client_email: str | None = None
    client_phone: str | None = None
    order_number: str | None = None
    text: str | None = None
    category: str | None = None
    confidence: str | None = None
    escalate: bool | None = None
    draft_reply: str | None = None
    error: str | None = None


class ClientRecord(BaseModel):
    client_id: str
    name: str | None = None
    email: str | None = None
    phone: str | None = None
    updated_at: str
