from contextlib import asynccontextmanager
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
import hashlib
import re
from pathlib import Path

import jwt
import httpx
import cv2
import numpy as np
from rapidocr_onnxruntime import RapidOCR
from fastapi import Depends, FastAPI, File, HTTPException, UploadFile, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .database import create_tables, get_session
from .models import FoodItem, Household, InventoryBatch, InventoryLedger, KnowledgeDocument, MealPlan, Membership, Receipt, ReceiptLineDraft, User
from .schemas import (
    AuthResponse, BatchCreate, BatchRead, ConsumeRequest, FoodCreate, FoodRead,
    AssistantRequest, KnowledgeCreate, LedgerRead, LoginRequest, PlanRequest, ReceiptConfirmRequest, ReceiptParseRequest, RegisterRequest,
)
from .security import create_access_token, decode_access_token, hash_password, verify_password

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    await create_tables()
    yield


app = FastAPI(title="FreshLoop API", version="0.2.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health", tags=["system"])
async def health() -> dict[str, str]:
    return {"status": "ok", "environment": settings.app_env}


bearer = HTTPBearer(auto_error=False)
ocr_engine: RapidOCR | None = None


async def current_context(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    session: AsyncSession = Depends(get_session),
) -> tuple[User, Household]:
    if not credentials:
        raise HTTPException(status_code=401, detail="请先登录")
    try:
        user_id = decode_access_token(credentials.credentials)
    except (jwt.InvalidTokenError, KeyError):
        raise HTTPException(status_code=401, detail="登录已失效") from None
    user = await session.get(User, user_id)
    membership = await session.scalar(select(Membership).where(Membership.user_id == user_id))
    if not user or not membership:
        raise HTTPException(status_code=401, detail="用户不存在")
    household = await session.get(Household, membership.household_id)
    if not household:
        raise HTTPException(status_code=401, detail="家庭空间不存在")
    return user, household


async def current_admin(context: tuple[User, Household] = Depends(current_context)) -> tuple[User, Household]:
    user, household = context
    allowed = {email.strip().lower() for email in settings.admin_emails.split(",") if email.strip()}
    if user.email.lower() not in allowed:
        raise HTTPException(status_code=403, detail="需要系统管理员权限")
    return user, household


def auth_payload(user: User, household: Household) -> AuthResponse:
    return AuthResponse(
        access_token=create_access_token(user.id),
        user={"id": user.id, "email": user.email, "display_name": user.display_name},
        household={"id": household.id, "name": household.name},
    )


@app.post("/api/auth/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
async def register(payload: RegisterRequest, session: AsyncSession = Depends(get_session)) -> AuthResponse:
    email = payload.email.lower()
    if await session.scalar(select(User).where(User.email == email)):
        raise HTTPException(status_code=409, detail="该邮箱已注册")
    user = User(email=email, display_name=payload.display_name, password_hash=hash_password(payload.password))
    session.add(user)
    await session.flush()
    household = Household(name=payload.household_name, owner_id=user.id)
    session.add(household)
    await session.flush()
    session.add(Membership(user_id=user.id, household_id=household.id, role="owner"))
    await session.commit()
    return auth_payload(user, household)


@app.post("/api/auth/login", response_model=AuthResponse)
async def login(payload: LoginRequest, session: AsyncSession = Depends(get_session)) -> AuthResponse:
    user = await session.scalar(select(User).where(User.email == payload.email.lower()))
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="邮箱或密码错误")
    membership = await session.scalar(select(Membership).where(Membership.user_id == user.id))
    household = await session.get(Household, membership.household_id) if membership else None
    if not household:
        raise HTTPException(status_code=409, detail="账户尚未加入家庭空间")
    return auth_payload(user, household)


@app.get("/api/me")
async def me(context: tuple[User, Household] = Depends(current_context)) -> dict:
    user, household = context
    return {"user": {"id": user.id, "email": user.email, "display_name": user.display_name}, "household": {"id": household.id, "name": household.name}}


@app.get("/api/foods", response_model=list[FoodRead])
async def list_foods(context: tuple[User, Household] = Depends(current_context), session: AsyncSession = Depends(get_session)):
    _, household = context
    return list((await session.scalars(select(FoodItem).where(FoodItem.household_id == household.id).order_by(FoodItem.name))).all())


@app.post("/api/foods", response_model=FoodRead, status_code=201)
async def create_food(payload: FoodCreate, context: tuple[User, Household] = Depends(current_context), session: AsyncSession = Depends(get_session)):
    _, household = context
    item = FoodItem(household_id=household.id, **payload.model_dump())
    session.add(item)
    await session.commit()
    await session.refresh(item)
    return item


@app.delete("/api/foods/{food_id}", status_code=204)
async def delete_food(food_id: str, context: tuple[User, Household] = Depends(current_context), session: AsyncSession = Depends(get_session)):
    _, household = context
    food = await session.scalar(select(FoodItem).where(FoodItem.id == food_id, FoodItem.household_id == household.id))
    if not food:
        raise HTTPException(status_code=404, detail="食品不存在")
    batch = await session.scalar(select(InventoryBatch).where(InventoryBatch.food_item_id == food.id))
    if batch:
        raise HTTPException(status_code=409, detail="该食品已有库存记录，请先处理对应库存")
    await session.delete(food)
    await session.commit()


async def batch_rows(session: AsyncSession, household_id: str, include_empty: bool = False) -> list[BatchRead]:
    query = select(InventoryBatch, FoodItem).join(FoodItem, FoodItem.id == InventoryBatch.food_item_id).where(InventoryBatch.household_id == household_id)
    if not include_empty:
        query = query.where(InventoryBatch.remaining_quantity > 0)
    query = query.order_by(InventoryBatch.expires_on.asc().nullslast(), InventoryBatch.created_at)
    result = await session.execute(query)
    return [BatchRead(id=batch.id, food_item_id=batch.food_item_id, food_name=food.name, category=food.category, quantity=batch.quantity, remaining_quantity=batch.remaining_quantity, unit=batch.unit, purchased_on=batch.purchased_on, expires_on=batch.expires_on, price=batch.price) for batch, food in result.all()]


@app.get("/api/inventory", response_model=list[BatchRead])
async def list_inventory(context: tuple[User, Household] = Depends(current_context), session: AsyncSession = Depends(get_session)):
    return await batch_rows(session, context[1].id)


@app.post("/api/inventory", response_model=BatchRead, status_code=201)
async def add_inventory(payload: BatchCreate, context: tuple[User, Household] = Depends(current_context), session: AsyncSession = Depends(get_session)):
    _, household = context
    food = await session.scalar(select(FoodItem).where(FoodItem.id == payload.food_item_id, FoodItem.household_id == household.id))
    if not food:
        raise HTTPException(status_code=404, detail="食品不存在")
    if payload.expires_on and payload.expires_on < payload.purchased_on:
        raise HTTPException(status_code=422, detail="到期日不能早于购买日")
    batch = InventoryBatch(household_id=household.id, remaining_quantity=payload.quantity, **payload.model_dump())
    session.add(batch)
    await session.flush()
    session.add(InventoryLedger(household_id=household.id, food_item_id=food.id, batch_id=batch.id, event_type="inbound", quantity_delta=payload.quantity, note="手动入库"))
    await session.commit()
    return BatchRead(id=batch.id, food_item_id=food.id, food_name=food.name, category=food.category, quantity=batch.quantity, remaining_quantity=batch.remaining_quantity, unit=batch.unit, purchased_on=batch.purchased_on, expires_on=batch.expires_on, price=batch.price)


@app.post("/api/inventory/{batch_id}/consume", response_model=BatchRead)
async def consume_inventory(batch_id: str, payload: ConsumeRequest, context: tuple[User, Household] = Depends(current_context), session: AsyncSession = Depends(get_session)):
    _, household = context
    batch = await session.scalar(select(InventoryBatch).where(InventoryBatch.id == batch_id, InventoryBatch.household_id == household.id).with_for_update())
    if not batch:
        raise HTTPException(status_code=404, detail="库存批次不存在")
    if payload.quantity > batch.remaining_quantity:
        raise HTTPException(status_code=409, detail="操作数量超过当前库存")
    food = await session.get(FoodItem, batch.food_item_id)
    batch.remaining_quantity -= payload.quantity
    session.add(InventoryLedger(household_id=household.id, food_item_id=batch.food_item_id, batch_id=batch.id, event_type=payload.event_type, quantity_delta=-payload.quantity, note=payload.note))
    await session.commit()
    return BatchRead(id=batch.id, food_item_id=batch.food_item_id, food_name=food.name, category=food.category, quantity=batch.quantity, remaining_quantity=batch.remaining_quantity, unit=batch.unit, purchased_on=batch.purchased_on, expires_on=batch.expires_on, price=batch.price)


@app.delete("/api/inventory/{batch_id}", status_code=204)
async def delete_inventory(batch_id: str, context: tuple[User, Household] = Depends(current_context), session: AsyncSession = Depends(get_session)):
    _, household = context
    batch = await session.scalar(select(InventoryBatch).where(InventoryBatch.id == batch_id, InventoryBatch.household_id == household.id))
    if not batch:
        raise HTTPException(status_code=404, detail="库存批次不存在")
    events = (await session.scalars(select(InventoryLedger).where(InventoryLedger.batch_id == batch.id))).all()
    if any(event.event_type != "inbound" for event in events):
        raise HTTPException(status_code=409, detail="该批次已有消费或丢弃记录，不能直接删除")
    await session.execute(delete(InventoryLedger).where(InventoryLedger.batch_id == batch.id))
    await session.delete(batch)
    await session.commit()


@app.get("/api/ledger", response_model=list[LedgerRead])
async def list_ledger(context: tuple[User, Household] = Depends(current_context), session: AsyncSession = Depends(get_session)):
    _, household = context
    result = await session.execute(select(InventoryLedger, FoodItem).join(FoodItem, FoodItem.id == InventoryLedger.food_item_id).where(InventoryLedger.household_id == household.id).order_by(InventoryLedger.created_at.desc()).limit(50))
    return [LedgerRead(id=row.id, food_name=food.name, event_type=row.event_type, quantity_delta=row.quantity_delta, note=row.note, created_at=row.created_at) for row, food in result.all()]


@app.get("/api/dashboard")
async def dashboard(context: tuple[User, Household] = Depends(current_context), session: AsyncSession = Depends(get_session)) -> dict:
    _, household = context
    today = date.today()
    expiry_limit = today + timedelta(days=3)
    batches = (await session.scalars(select(InventoryBatch).where(InventoryBatch.household_id == household.id, InventoryBatch.remaining_quantity > 0))).all()
    return {
        "active_batches": len(batches),
        "total_quantity": round(sum(batch.remaining_quantity for batch in batches), 2),
        "expiring_batches": sum(1 for batch in batches if batch.expires_on and today <= batch.expires_on <= expiry_limit),
        "expired_batches": sum(1 for batch in batches if batch.expires_on and batch.expires_on < today),
        "inventory_value": round(sum((batch.price or 0) * (batch.remaining_quantity / batch.quantity) for batch in batches), 2),
    }


@app.get("/api/analytics")
async def analytics(context: tuple[User, Household] = Depends(current_context), session: AsyncSession = Depends(get_session)) -> dict:
    _, household = context
    since = datetime.now(timezone.utc) - timedelta(days=13)
    result = await session.execute(
        select(InventoryLedger, FoodItem)
        .join(FoodItem, FoodItem.id == InventoryLedger.food_item_id)
        .where(InventoryLedger.household_id == household.id, InventoryLedger.created_at >= since)
        .order_by(InventoryLedger.created_at)
    )
    daily: dict[str, dict[str, float]] = {
        (date.today() - timedelta(days=offset)).isoformat(): {"consume": 0, "discard": 0}
        for offset in range(13, -1, -1)
    }
    by_food: dict[str, dict[str, float]] = defaultdict(lambda: {"consume": 0, "discard": 0})
    for row, food in result.all():
        if row.event_type not in {"consume", "discard"}:
            continue
        key = row.created_at.date().isoformat()
        amount = abs(row.quantity_delta)
        if key in daily:
            daily[key][row.event_type] += amount
        by_food[food.name][row.event_type] += amount
    consumed = sum(value["consume"] for value in daily.values())
    discarded = sum(value["discard"] for value in daily.values())
    denominator = consumed + discarded
    return {
        "consumed": round(consumed, 2),
        "discarded": round(discarded, 2),
        "waste_rate": round(discarded / denominator * 100, 1) if denominator else 0,
        "daily": [{"date": key, **value} for key, value in daily.items()],
        "by_food": [{"name": key, **value} for key, value in sorted(by_food.items(), key=lambda item: sum(item[1].values()), reverse=True)[:8]],
    }


@app.post("/api/plans", status_code=201)
async def generate_plan(payload: PlanRequest, context: tuple[User, Household] = Depends(current_context), session: AsyncSession = Depends(get_session)) -> dict:
    _, household = context
    rows = (await session.execute(
        select(InventoryBatch, FoodItem)
        .join(FoodItem, FoodItem.id == InventoryBatch.food_item_id)
        .where(InventoryBatch.household_id == household.id, InventoryBatch.remaining_quantity > 0)
        .order_by(InventoryBatch.expires_on.asc().nullslast(), InventoryBatch.created_at)
    )).all()
    excluded = {item.strip().lower() for item in payload.dislikes if item.strip()}
    available = [(batch, food) for batch, food in rows if food.name.lower() not in excluded]
    menu = []
    used_names: list[str] = []
    for day_index in range(payload.days):
        selected = available[day_index * 2:day_index * 2 + 2] or available[:2]
        ingredients = [{"name": food.name, "amount": min(batch.remaining_quantity, max(1, payload.people)), "unit": batch.unit, "expires_on": batch.expires_on.isoformat() if batch.expires_on else None} for batch, food in selected]
        used_names.extend(item["name"] for item in ingredients)
        menu.append({"day": day_index + 1, "title": " · ".join(item["name"] for item in ingredients) if ingredients else "待采购食材组合", "ingredients": ingredients, "cooking_minutes": min(payload.max_cooking_minutes, 20 + day_index * 5)})
    estimated_purchase = max(0, round(payload.days * payload.people * 18 - sum((batch.price or 0) * batch.remaining_quantity / batch.quantity for batch, _ in available), 2))
    warnings = []
    if not available:
        warnings.append("当前没有可用库存，计划需要先补充食材")
    if estimated_purchase > payload.budget:
        warnings.append(f"预计采购 ¥{estimated_purchase:.2f}，超过预算 ¥{payload.budget:.2f}")
    expiring_used = sorted({food.name for batch, food in available if batch.expires_on and batch.expires_on <= date.today() + timedelta(days=3) and food.name in used_names})
    result = {"menu": menu, "estimated_purchase": estimated_purchase, "budget": payload.budget, "expiring_used": expiring_used, "warnings": warnings}
    plan = MealPlan(household_id=household.id, days=payload.days, people=payload.people, budget=payload.budget, constraints={"dislikes": payload.dislikes, "max_cooking_minutes": payload.max_cooking_minutes}, result=result)
    session.add(plan)
    await session.commit()
    return {"id": plan.id, "status": plan.status, "created_at": plan.created_at, **result}


@app.get("/api/plans")
async def list_plans(context: tuple[User, Household] = Depends(current_context), session: AsyncSession = Depends(get_session)) -> list[dict]:
    _, household = context
    plans = (await session.scalars(select(MealPlan).where(MealPlan.household_id == household.id).order_by(MealPlan.created_at.desc()).limit(10))).all()
    return [{"id": plan.id, "status": plan.status, "days": plan.days, "people": plan.people, "budget": plan.budget, "created_at": plan.created_at, **plan.result} for plan in plans]


@app.post("/api/plans/{plan_id}/confirm")
async def confirm_plan(plan_id: str, context: tuple[User, Household] = Depends(current_context), session: AsyncSession = Depends(get_session)) -> dict:
    _, household = context
    plan = await session.scalar(select(MealPlan).where(MealPlan.id == plan_id, MealPlan.household_id == household.id))
    if not plan:
        raise HTTPException(status_code=404, detail="计划不存在")
    plan.status = "confirmed"
    await session.commit()
    return {"id": plan.id, "status": plan.status}


@app.delete("/api/plans/{plan_id}", status_code=204)
async def delete_plan(plan_id: str, context: tuple[User, Household] = Depends(current_context), session: AsyncSession = Depends(get_session)):
    _, household = context
    plan = await session.scalar(select(MealPlan).where(MealPlan.id == plan_id, MealPlan.household_id == household.id))
    if not plan:
        raise HTTPException(status_code=404, detail="计划不存在")
    await session.delete(plan)
    await session.commit()


def parse_receipt_text(raw_text: str) -> list[dict]:
    lines: list[dict] = []
    pattern = re.compile(r"^\s*(?P<name>[\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9 ()（）·_-]{0,80}?)\s+(?P<quantity>\d+(?:\.\d+)?)\s*(?P<unit>kg|g|千克|克|个|盒|袋|瓶|包|份|枚)?\s+(?P<price>\d+(?:\.\d+)?)\s*$", re.IGNORECASE)
    price_only = re.compile(r"^\s*(?P<name>[\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9 ()（）·_-]{0,80}?)\s+(?P<price>\d+(?:\.\d+)?)\s*$")
    for raw_line in raw_text.splitlines():
        match = pattern.match(raw_line)
        if match:
            data = match.groupdict()
            lines.append({"name": data["name"].strip(), "quantity": float(data["quantity"]), "unit": data["unit"] or "份", "price": float(data["price"]), "confidence": 0.92})
            continue
        match = price_only.match(raw_line)
        if match:
            data = match.groupdict()
            lines.append({"name": data["name"].strip(), "quantity": 1, "unit": "份", "price": float(data["price"]), "confidence": 0.7})
    return lines


def get_ocr_engine() -> RapidOCR:
    global ocr_engine
    if ocr_engine is None:
        ocr_engine = RapidOCR()
    return ocr_engine


@app.post("/api/receipts/parse", status_code=201)
async def parse_receipt(payload: ReceiptParseRequest, context: tuple[User, Household] = Depends(current_context), session: AsyncSession = Depends(get_session)) -> dict:
    _, household = context
    content_hash = hashlib.sha256(payload.raw_text.strip().encode()).hexdigest()
    existing = await session.scalar(select(Receipt).where(Receipt.household_id == household.id, Receipt.content_hash == content_hash))
    if existing:
        raise HTTPException(status_code=409, detail="这张小票已导入，请在草稿列表中继续处理")
    parsed = parse_receipt_text(payload.raw_text)
    if not parsed:
        raise HTTPException(status_code=422, detail="未识别到商品行。每行格式示例：西红柿 2 个 8.50")
    receipt = Receipt(household_id=household.id, raw_text=payload.raw_text.strip(), content_hash=content_hash)
    session.add(receipt)
    await session.flush()
    drafts = [ReceiptLineDraft(receipt_id=receipt.id, **line) for line in parsed]
    session.add_all(drafts)
    await session.commit()
    return {"id": receipt.id, "status": receipt.status, "lines": [{"id": line.id, "name": line.name, "quantity": line.quantity, "unit": line.unit, "price": line.price, "confidence": line.confidence} for line in drafts]}


@app.post("/api/receipts/image", status_code=201)
async def parse_receipt_image(image: UploadFile = File(...), context: tuple[User, Household] = Depends(current_context), session: AsyncSession = Depends(get_session)) -> dict:
    _, household = context
    if image.content_type not in {"image/jpeg", "image/png", "image/webp"}:
        raise HTTPException(status_code=415, detail="仅支持 JPG、PNG 或 WebP 格式的小票照片")
    content = await image.read()
    if not content or len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="图片不能为空且不能超过 10MB")
    content_hash = hashlib.sha256(content).hexdigest()
    existing = await session.scalar(select(Receipt).where(Receipt.household_id == household.id, Receipt.content_hash == content_hash))
    if existing:
        raise HTTPException(status_code=409, detail="这张小票已导入，请在草稿列表中继续处理")
    decoded = cv2.imdecode(np.frombuffer(content, np.uint8), cv2.IMREAD_COLOR)
    if decoded is None:
        raise HTTPException(status_code=422, detail="无法读取这张图片")
    result, _ = get_ocr_engine()(decoded)
    raw_text = "\n".join(item[1] for item in result or [])
    parsed = parse_receipt_text(raw_text)
    if not parsed:
        raise HTTPException(status_code=422, detail="已识别文字，但未提取到商品行。请改用文字导入并核对 OCR 内容。")
    upload_dir = Path("uploads")
    upload_dir.mkdir(exist_ok=True)
    suffix = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}[image.content_type]
    (upload_dir / f"{content_hash}{suffix}").write_bytes(content)
    receipt = Receipt(household_id=household.id, raw_text=raw_text, content_hash=content_hash)
    session.add(receipt)
    await session.flush()
    drafts = [ReceiptLineDraft(receipt_id=receipt.id, **line) for line in parsed]
    session.add_all(drafts)
    await session.commit()
    return {"id": receipt.id, "status": receipt.status, "ocr_text": raw_text, "lines": [{"id": line.id, "name": line.name, "quantity": line.quantity, "unit": line.unit, "price": line.price, "confidence": line.confidence} for line in drafts]}


@app.get("/api/receipts")
async def list_receipts(context: tuple[User, Household] = Depends(current_context), session: AsyncSession = Depends(get_session)) -> list[dict]:
    _, household = context
    receipts = (await session.scalars(select(Receipt).where(Receipt.household_id == household.id).order_by(Receipt.created_at.desc()).limit(20))).all()
    output = []
    for receipt in receipts:
        lines = (await session.scalars(select(ReceiptLineDraft).where(ReceiptLineDraft.receipt_id == receipt.id))).all()
        output.append({"id": receipt.id, "status": receipt.status, "created_at": receipt.created_at, "lines": [{"id": line.id, "name": line.name, "quantity": line.quantity, "unit": line.unit, "price": line.price, "confidence": line.confidence} for line in lines]})
    return output


@app.delete("/api/receipts/{receipt_id}", status_code=204)
async def delete_receipt(receipt_id: str, context: tuple[User, Household] = Depends(current_context), session: AsyncSession = Depends(get_session)):
    _, household = context
    receipt = await session.scalar(select(Receipt).where(Receipt.id == receipt_id, Receipt.household_id == household.id))
    if not receipt:
        raise HTTPException(status_code=404, detail="小票草稿不存在")
    if receipt.status != "draft":
        raise HTTPException(status_code=409, detail="已确认入库的小票不能删除")
    await session.execute(delete(ReceiptLineDraft).where(ReceiptLineDraft.receipt_id == receipt.id))
    await session.delete(receipt)
    await session.commit()


@app.post("/api/receipts/{receipt_id}/confirm")
async def confirm_receipt(receipt_id: str, payload: ReceiptConfirmRequest, context: tuple[User, Household] = Depends(current_context), session: AsyncSession = Depends(get_session)) -> dict:
    _, household = context
    receipt = await session.scalar(select(Receipt).where(Receipt.id == receipt_id, Receipt.household_id == household.id))
    if not receipt:
        raise HTTPException(status_code=404, detail="小票草稿不存在")
    if receipt.status != "draft":
        raise HTTPException(status_code=409, detail="这张小票已经确认入库")
    if payload.expires_on and payload.expires_on < payload.purchased_on:
        raise HTTPException(status_code=422, detail="到期日不能早于购买日")
    created_batches = []
    for line in payload.lines:
        food = await session.scalar(select(FoodItem).where(FoodItem.household_id == household.id, FoodItem.name == line.name))
        if not food:
            food = FoodItem(household_id=household.id, name=line.name, category=line.category, default_unit=line.unit)
            session.add(food)
            await session.flush()
        batch = InventoryBatch(household_id=household.id, food_item_id=food.id, quantity=line.quantity, remaining_quantity=line.quantity, unit=line.unit, purchased_on=payload.purchased_on, expires_on=payload.expires_on, price=line.price)
        session.add(batch)
        await session.flush()
        session.add(InventoryLedger(household_id=household.id, food_item_id=food.id, batch_id=batch.id, event_type="inbound", quantity_delta=line.quantity, note=f"小票 {receipt.id[:8]} 入库"))
        created_batches.append(batch.id)
    receipt.status = "confirmed"
    await session.commit()
    return {"receipt_id": receipt.id, "status": receipt.status, "batch_ids": created_batches}


@app.get("/api/knowledge")
async def list_knowledge(context: tuple[User, Household] = Depends(current_context), session: AsyncSession = Depends(get_session)) -> list[dict]:
    _, household = context
    docs = (await session.scalars(select(KnowledgeDocument).where(KnowledgeDocument.household_id == household.id).order_by(KnowledgeDocument.created_at.desc()))).all()
    return [{"id": doc.id, "title": doc.title, "source_url": doc.source_url, "content": doc.content, "created_at": doc.created_at} for doc in docs]


@app.post("/api/knowledge", status_code=201)
async def create_knowledge(payload: KnowledgeCreate, context: tuple[User, Household] = Depends(current_context), session: AsyncSession = Depends(get_session)) -> dict:
    _, household = context
    doc = KnowledgeDocument(household_id=household.id, **payload.model_dump())
    session.add(doc)
    await session.commit()
    return {"id": doc.id, "title": doc.title, "source_url": doc.source_url, "content": doc.content, "created_at": doc.created_at}


@app.get("/api/knowledge/search")
async def search_knowledge(q: str, context: tuple[User, Household] = Depends(current_context), session: AsyncSession = Depends(get_session)) -> list[dict]:
    _, household = context
    terms = [term for term in re.split(r"\s+", q.strip()) if term]
    docs = (await session.scalars(select(KnowledgeDocument).where(KnowledgeDocument.household_id == household.id))).all()
    scored = [(sum((doc.title + doc.content).lower().count(term.lower()) for term in terms), doc) for doc in docs]
    return [{"id": doc.id, "title": doc.title, "source_url": doc.source_url, "excerpt": doc.content[:240]} for score, doc in sorted(scored, key=lambda item: item[0], reverse=True) if score > 0][:5]


@app.post("/api/assistant")
async def assistant_answer(payload: AssistantRequest, context: tuple[User, Household] = Depends(current_context), session: AsyncSession = Depends(get_session)) -> dict:
    _, household = context
    question = payload.question.strip()
    batches = (await session.execute(select(InventoryBatch, FoodItem).join(FoodItem, FoodItem.id == InventoryBatch.food_item_id).where(InventoryBatch.household_id == household.id, InventoryBatch.remaining_quantity > 0).order_by(InventoryBatch.expires_on.asc().nullslast()))).all()
    citations: list[dict] = []
    docs = (await session.scalars(select(KnowledgeDocument).where(KnowledgeDocument.household_id == household.id))).all()
    if settings.llm_api_key:
        inventory_context = "\n".join(f"- {food.name}: {batch.remaining_quantity}{batch.unit}, 到期日：{batch.expires_on or '未设置'}" for batch, food in batches) or "暂无库存"
        knowledge_context = "\n\n".join(f"【{doc.title}】\n{doc.content[:800]}" for doc in docs[:8]) or "暂无知识资料"
        citations = [{"title": doc.title, "source_url": doc.source_url, "excerpt": doc.content[:180]} for doc in docs[:3]]
        system_prompt = "你是 FreshLoop 家庭食品管理助手。只能基于提供的库存和知识资料回答；库存数据为实时事实。不要编造食品安全结论，不要修改库存。回答使用简洁中文，并明确指出临期食材。"
        user_prompt = f"当前库存：\n{inventory_context}\n\n知识资料：\n{knowledge_context}\n\n用户问题：{question}"
        try:
            async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
                response = await client.post(
                    f"{settings.llm_base_url.rstrip('/')}/chat/completions",
                    headers={"Authorization": f"Bearer {settings.llm_api_key}"},
                    json={"model": settings.llm_model, "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}], "temperature": 0.3},
                )
            response.raise_for_status()
            answer = response.json()["choices"][0]["message"]["content"]
            return {"answer": answer, "citations": citations, "mode": "llm"}
        except (httpx.HTTPError, KeyError, IndexError, TypeError) as exc:
            raise HTTPException(status_code=502, detail=f"AI 服务调用失败：{exc}") from None
    if any(word in question for word in ("临期", "到期", "快过期")):
        urgent = [food.name for batch, food in batches if batch.expires_on and batch.expires_on <= date.today() + timedelta(days=3)]
        answer = f"3 天内临期的食品有：{'、'.join(urgent)}。建议优先安排这些食材。" if urgent else "目前没有 3 天内临期的食品。"
    elif any(word in question for word in ("库存", "有什么", "食材")):
        answer = "当前可用库存：" + "、".join(f"{food.name} {batch.remaining_quantity}{batch.unit}" for batch, food in batches[:8]) if batches else "当前没有可用库存。"
    else:
        scored = [(sum((doc.title + doc.content).lower().count(term.lower()) for term in re.split(r"\s+", question) if term), doc) for doc in docs]
        matches = [doc for score, doc in sorted(scored, key=lambda item: item[0], reverse=True) if score > 0][:3]
        citations = [{"title": doc.title, "source_url": doc.source_url, "excerpt": doc.content[:180]} for doc in matches]
        answer = matches[0].content[:300] if matches else "我可以查询库存和临期食品，也可以基于你添加的知识资料回答问题。"
    return {"answer": answer, "citations": citations}


@app.get("/api/export")
async def export_household_data(context: tuple[User, Household] = Depends(current_context), session: AsyncSession = Depends(get_session)) -> JSONResponse:
    _, household = context
    foods = (await session.scalars(select(FoodItem).where(FoodItem.household_id == household.id))).all()
    batches = (await session.scalars(select(InventoryBatch).where(InventoryBatch.household_id == household.id))).all()
    ledger = (await session.scalars(select(InventoryLedger).where(InventoryLedger.household_id == household.id).order_by(InventoryLedger.created_at))).all()
    receipts = (await session.scalars(select(Receipt).where(Receipt.household_id == household.id))).all()
    plans = (await session.scalars(select(MealPlan).where(MealPlan.household_id == household.id))).all()
    documents = (await session.scalars(select(KnowledgeDocument).where(KnowledgeDocument.household_id == household.id))).all()
    payload = {
        "exported_at": datetime.now(timezone.utc),
        "household": {"id": household.id, "name": household.name},
        "foods": [{"id": food.id, "name": food.name, "category": food.category, "default_unit": food.default_unit, "created_at": food.created_at} for food in foods],
        "inventory_batches": [{"id": batch.id, "food_item_id": batch.food_item_id, "quantity": batch.quantity, "remaining_quantity": batch.remaining_quantity, "unit": batch.unit, "purchased_on": batch.purchased_on, "expires_on": batch.expires_on, "price": batch.price} for batch in batches],
        "inventory_ledger": [{"food_item_id": row.food_item_id, "batch_id": row.batch_id, "event_type": row.event_type, "quantity_delta": row.quantity_delta, "note": row.note, "created_at": row.created_at} for row in ledger],
        "receipts": [{"id": receipt.id, "status": receipt.status, "raw_text": receipt.raw_text, "created_at": receipt.created_at} for receipt in receipts],
        "meal_plans": [{"id": plan.id, "status": plan.status, "constraints": plan.constraints, "result": plan.result, "created_at": plan.created_at} for plan in plans],
        "knowledge_documents": [{"title": document.title, "source_url": document.source_url, "content": document.content, "created_at": document.created_at} for document in documents],
    }
    filename = f"freshloop-{date.today().isoformat()}.json"
    return JSONResponse(content=jsonable_encoder(payload), headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.get("/api/admin/overview")
async def admin_overview(_: tuple[User, Household] = Depends(current_admin), session: AsyncSession = Depends(get_session)) -> dict:
    users = (await session.scalars(select(User).order_by(User.created_at.desc()))).all()
    households = (await session.scalars(select(Household).order_by(Household.created_at.desc()))).all()
    receipts = (await session.scalars(select(Receipt).order_by(Receipt.created_at.desc()).limit(30))).all()
    batches = (await session.scalars(select(InventoryBatch).where(InventoryBatch.remaining_quantity > 0))).all()
    user_rows = []
    for user in users:
        membership = await session.scalar(select(Membership).where(Membership.user_id == user.id))
        household = await session.get(Household, membership.household_id) if membership else None
        user_rows.append({"id": user.id, "email": user.email, "display_name": user.display_name, "household": household.name if household else "未分配", "created_at": user.created_at})
    return jsonable_encoder({
        "stats": {"users": len(users), "households": len(households), "active_batches": len(batches), "receipts": len(receipts)},
        "users": user_rows,
        "households": [{"id": item.id, "name": item.name, "owner_id": item.owner_id, "created_at": item.created_at} for item in households],
        "receipts": [{"id": item.id, "household_id": item.household_id, "status": item.status, "created_at": item.created_at} for item in receipts],
    })
