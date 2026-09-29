from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app


def test_complete_inventory_flow() -> None:
    email = f"user-{uuid4().hex}@example.com"
    with TestClient(app) as client:
        register = client.post(
            "/api/auth/register",
            json={
                "email": email,
                "password": "strong-password",
                "display_name": "测试用户",
                "household_name": "测试家庭",
            },
        )
        assert register.status_code == 201
        token = register.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        food = client.post(
            "/api/foods",
            headers=headers,
            json={"name": "西红柿", "category": "蔬菜", "default_unit": "个"},
        )
        assert food.status_code == 201

        batch = client.post(
            "/api/inventory",
            headers=headers,
            json={
                "food_item_id": food.json()["id"],
                "quantity": 5,
                "unit": "个",
                "purchased_on": "2026-09-24",
                "expires_on": "2026-09-27",
                "price": 12.5,
            },
        )
        assert batch.status_code == 201
        assert batch.json()["remaining_quantity"] == 5

        consumed = client.post(
            f"/api/inventory/{batch.json()['id']}/consume",
            headers=headers,
            json={"quantity": 2, "event_type": "consume", "note": "晚餐"},
        )
        assert consumed.status_code == 200
        assert consumed.json()["remaining_quantity"] == 3

        inventory = client.get("/api/inventory", headers=headers)
        ledger = client.get("/api/ledger", headers=headers)
        assert inventory.status_code == 200
        assert inventory.json()[0]["remaining_quantity"] == 3
        assert [row["event_type"] for row in ledger.json()] == ["consume", "inbound"]

        analysis = client.get("/api/analytics", headers=headers)
        assert analysis.status_code == 200
        assert analysis.json()["consumed"] == 2

        plan = client.post(
            "/api/plans",
            headers=headers,
            json={"days": 3, "people": 2, "budget": 100, "dislikes": [], "max_cooking_minutes": 30},
        )
        assert plan.status_code == 201
        assert len(plan.json()["menu"]) == 3
        confirmed = client.post(f"/api/plans/{plan.json()['id']}/confirm", headers=headers)
        assert confirmed.json()["status"] == "confirmed"


def test_household_data_is_isolated() -> None:
    with TestClient(app) as client:
        tokens = []
        for index in range(2):
            response = client.post(
                "/api/auth/register",
                json={"email": f"isolated-{uuid4().hex}@example.com", "password": "strong-password", "display_name": f"用户{index}", "household_name": f"家庭{index}"},
            )
            tokens.append(response.json()["access_token"])
        first_headers = {"Authorization": f"Bearer {tokens[0]}"}
        second_headers = {"Authorization": f"Bearer {tokens[1]}"}
        client.post("/api/foods", headers=first_headers, json={"name": "私有食品", "category": "其他", "default_unit": "份"})
        assert len(client.get("/api/foods", headers=first_headers).json()) == 1
        assert client.get("/api/foods", headers=second_headers).json() == []


def test_receipt_draft_can_be_confirmed_once() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/api/auth/register",
            json={"email": f"receipt-{uuid4().hex}@example.com", "password": "strong-password", "display_name": "收据用户", "household_name": "收据家庭"},
        )
        headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
        raw_text = "西红柿 2 个 8.50\n鸡蛋 10 枚 15.80"
        draft = client.post("/api/receipts/parse", headers=headers, json={"raw_text": raw_text})
        assert draft.status_code == 201
        assert len(draft.json()["lines"]) == 2
        duplicate = client.post("/api/receipts/parse", headers=headers, json={"raw_text": raw_text})
        assert duplicate.status_code == 409
        confirmed = client.post(
            f"/api/receipts/{draft.json()['id']}/confirm",
            headers=headers,
            json={"purchased_on": "2026-09-24", "lines": draft.json()["lines"]},
        )
        assert confirmed.status_code == 200
        assert len(client.get("/api/inventory", headers=headers).json()) == 2
        again = client.post(
            f"/api/receipts/{draft.json()['id']}/confirm",
            headers=headers,
            json={"purchased_on": "2026-09-24", "lines": draft.json()["lines"]},
        )
        assert again.status_code == 409
