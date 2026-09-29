from collections.abc import AsyncIterator
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .config import get_settings
from .models import Base, FoodItem, Household, InventoryBatch, InventoryLedger, Membership, User
from .security import hash_password

settings = get_settings()
engine = create_async_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


async def create_tables() -> None:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    if settings.app_env == "development":
        await seed_demo_data()


async def seed_demo_data() -> None:
    async with SessionLocal() as session:
        if await session.scalar(select(User).where(User.email == "demo@freshloop.app")):
            return
        user = User(email="demo@freshloop.app", display_name="体验用户", password_hash=hash_password("freshloop123"))
        session.add(user)
        await session.flush()
        household = Household(name="我的家庭", owner_id=user.id)
        session.add(household)
        await session.flush()
        session.add(Membership(user_id=user.id, household_id=household.id, role="owner"))
        samples = [
            ("西红柿", "蔬菜", "个", 5, 12.5, 2),
            ("鸡蛋", "肉蛋奶", "枚", 10, 15.8, 7),
            ("鲜牛奶", "肉蛋奶", "盒", 3, 18.0, 1),
            ("苹果", "水果", "个", 6, 22.0, 10),
        ]
        for name, category, unit, quantity, price, expires_in in samples:
            food = FoodItem(household_id=household.id, name=name, category=category, default_unit=unit)
            session.add(food)
            await session.flush()
            batch = InventoryBatch(household_id=household.id, food_item_id=food.id, quantity=quantity, remaining_quantity=quantity, unit=unit, purchased_on=date.today(), expires_on=date.today() + timedelta(days=expires_in), price=price)
            session.add(batch)
            await session.flush()
            session.add(InventoryLedger(household_id=household.id, food_item_id=food.id, batch_id=batch.id, event_type="inbound", quantity_delta=quantity, note="演示数据"))
        await session.commit()
