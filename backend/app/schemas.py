from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    display_name: str = Field(min_length=1, max_length=80)
    household_name: str = Field(min_length=1, max_length=100)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict[str, str]
    household: dict[str, str]


class FoodCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    category: str = Field(default="其他", max_length=50)
    default_unit: str = Field(default="份", max_length=20)


class FoodRead(FoodCreate):
    model_config = ConfigDict(from_attributes=True)
    id: str
    created_at: datetime


class BatchCreate(BaseModel):
    food_item_id: str
    quantity: float = Field(gt=0)
    unit: str = Field(min_length=1, max_length=20)
    purchased_on: date = Field(default_factory=date.today)
    expires_on: date | None = None
    price: float | None = Field(default=None, ge=0)


class ConsumeRequest(BaseModel):
    quantity: float = Field(gt=0)
    event_type: str = Field(default="consume", pattern="^(consume|discard)$")
    note: str = Field(default="", max_length=255)


class BatchRead(BaseModel):
    id: str
    food_item_id: str
    food_name: str
    category: str
    quantity: float
    remaining_quantity: float
    unit: str
    purchased_on: date
    expires_on: date | None
    price: float | None


class LedgerRead(BaseModel):
    id: str
    food_name: str
    event_type: str
    quantity_delta: float
    note: str
    created_at: datetime


class PlanRequest(BaseModel):
    days: int = Field(default=3, ge=1, le=7)
    people: int = Field(default=2, ge=1, le=10)
    budget: float = Field(default=100, ge=0, le=10000)
    dislikes: list[str] = Field(default_factory=list)
    max_cooking_minutes: int = Field(default=30, ge=5, le=180)


class ReceiptParseRequest(BaseModel):
    raw_text: str = Field(min_length=3, max_length=20_000)


class ReceiptLineInput(BaseModel):
    id: str | None = None
    name: str = Field(min_length=1, max_length=120)
    quantity: float = Field(gt=0)
    unit: str = Field(min_length=1, max_length=20)
    price: float | None = Field(default=None, ge=0)
    category: str = Field(default="其他", max_length=50)


class ReceiptConfirmRequest(BaseModel):
    purchased_on: date = Field(default_factory=date.today)
    expires_on: date | None = None
    lines: list[ReceiptLineInput] = Field(min_length=1)


class KnowledgeCreate(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    source_url: str | None = Field(default=None, max_length=500)
    content: str = Field(min_length=10, max_length=50_000)


class AssistantRequest(BaseModel):
    question: str = Field(min_length=2, max_length=1_000)
