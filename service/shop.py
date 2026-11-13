import random
from datetime import datetime, timedelta

from consistances import CROPS_DATA, FERTILIZERS, SHOP_CONFIG, is_event_crop
from database.inventory_repo import InventoryRepository
from database.shop_repo import ShopRepository
from database.user_repo import UserRepository
from service.base import BaseService
from service.passive import apply_discount, get_effects
from service.quest import KST
from service.user import UserService
from utils.datatype import BuyResult, ShopBoard, ShopItem

CATEGORIES = ("seed", "fertilizer")
GRADES = (1, 2, 3, 4, 5)


# ------------------------------------------------------------ 입고 기간


def shop_period(now: datetime | None = None) -> tuple[str, int]:
    """(입고 기간 키, 다음 입고 Unix 타임스탬프). KST 0시 기준 refresh_hours 마다 교체"""
    hours = SHOP_CONFIG["refresh_hours"]
    now = (now or datetime.now(KST)).astimezone(KST)
    start = now.replace(
        hour=now.hour - now.hour % hours, minute=0, second=0, microsecond=0
    )
    return start.strftime("%Y-%m-%dT%H"), int((start + timedelta(hours=hours)).timestamp())


# ------------------------------------------------------------ 품목 정보


def item_category(item_id: str) -> str:
    return "seed" if item_id.endswith("_seed") else "fertilizer"


def item_grade(item_id: str) -> int:
    if item_category(item_id) == "seed":
        return CROPS_DATA[item_id.removesuffix("_seed")]["buy_grade"]
    return FERTILIZERS[item_id]["grade"]


def item_price(item_id: str) -> int:
    if item_category(item_id) == "seed":
        return CROPS_DATA[item_id.removesuffix("_seed")]["buy_price"]
    return FERTILIZERS[item_id]["price"]


def items_by_grade(category: str) -> dict[int, list[str]]:
    """등급 -> 판매 가능한 item_id 목록"""
    if category == "seed":
        # 이벤트 작물 씨앗은 상점에서 팔지 않는다
        items = [f"{crop}_seed" for crop in CROPS_DATA if not is_event_crop(crop)]
    else:
        items = list(FERTILIZERS)
    grouped: dict[int, list[str]] = {}
    for item_id in items:
        grouped.setdefault(item_grade(item_id), []).append(item_id)
    return grouped


# ------------------------------------------------------------ 레벨별 확률


def grade_weights(category: str, level: int) -> list[int]:
    """레벨 구간 표에서 해당 레벨의 등급 가중치 [최하급..최상급]"""
    table = SHOP_CONFIG["grade_weights"][category]
    entry = table[0]
    for row in table:
        if row["min_level"] <= level:
            entry = row
    return entry["weights"]


def grade_odds(category: str, level: int) -> dict[int, float]:
    """실제로 품목이 있는 등급만 고려한 등급별 등장 확률(%)"""
    weights = grade_weights(category, level)
    available = items_by_grade(category)
    usable = {g: weights[g - 1] for g in GRADES if weights[g - 1] > 0 and available.get(g)}
    total = sum(usable.values())
    return {g: w * 100 / total for g, w in usable.items()} if total else {}


def generate_stock(user_id: int, period: str, level: int) -> list[dict]:
    """레벨 확률표에 따라 진열 품목을 뽑는다.
    (유저, 기간) 으로 시드를 고정해서 같은 기간에는 항상 같은 결과 (중복 품목 없음)"""
    rng = random.Random(f"{user_id}:shop:{period}")
    stock: list[dict] = []
    for category in CATEGORIES:
        pool = {g: sorted(ids) for g, ids in items_by_grade(category).items()}
        weights = grade_weights(category, level)
        for _ in range(SHOP_CONFIG["slots"][category]):
            grades = [g for g in GRADES if weights[g - 1] > 0 and pool.get(g)]
            if not grades:
                break
            grade = rng.choices(grades, weights=[weights[g - 1] for g in grades])[0]
            item_id = rng.choice(pool[grade])
            pool[grade].remove(item_id)
            stock.append(
                {
                    "slot": len(stock) + 1,
                    "item_id": item_id,
                    "price": item_price(item_id),
                    "stock": SHOP_CONFIG["stock_by_grade"][category][grade - 1],
                }
            )
    return stock


# ---------------------------------------------------------------- 서비스


class NotEnoughGold(Exception):
    """트랜잭션 롤백용"""


class ShopService(BaseService):

    async def get_shop(self, user_id: int) -> ShopBoard:
        period, reset_at = shop_period()
        async with self.db.transaction() as conn:
            user = await UserRepository(conn).get_user(user_id)
            level = UserService.calculate_level(user["farm_exp"])
            repo = ShopRepository(conn)
            rows = await repo.get_items(user_id, period)
            if not rows:
                await repo.restock(user_id, period, generate_stock(user_id, period, level))
                rows = await repo.get_items(user_id, period)
            discount = (await get_effects(conn, user_id)).get("shop_discount_pct", 0)

        items = [
            ShopItem(
                slot=row["slot"],
                item_id=row["item_id"],
                category=item_category(row["item_id"]),
                grade=item_grade(row["item_id"]),
                price=apply_discount(row["price"], discount),
                stock=row["stock"],
                bought=row["bought"],
            )
            for row in rows
            if row["item_id"] in FERTILIZERS
            or row["item_id"].removesuffix("_seed") in CROPS_DATA  # json 에서 삭제된 품목 제외
        ]
        return ShopBoard(
            level=level,
            gold=user["gold"],
            items=items,
            reset_at=reset_at,
            discount_pct=discount,
        )

    async def buy(self, user_id: int, slot: int, amount: int) -> BuyResult:
        if amount < 1:
            return BuyResult(ok=False, reason="invalid", item_id=None, amount=0, cost=0)

        period, _ = shop_period()
        try:
            async with self.db.transaction() as conn:
                row = await ShopRepository(conn).buy(user_id, slot, period, amount)
                if row is None:
                    return BuyResult(
                        ok=False, reason="sold_out", item_id=None, amount=amount, cost=0
                    )

                discount = (await get_effects(conn, user_id)).get("shop_discount_pct", 0)
                cost = apply_discount(row["price"], discount) * amount
                if await UserRepository(conn).spend_gold(user_id, cost) is None:
                    raise NotEnoughGold  # 재고 차감까지 롤백

                await InventoryRepository(conn).update_inventory(
                    user_id, row["item_id"], amount
                )
                return BuyResult(
                    ok=True, reason=None, item_id=row["item_id"], amount=amount, cost=cost
                )
        except NotEnoughGold:
            return BuyResult(
                ok=False, reason="no_gold", item_id=row["item_id"], amount=amount, cost=cost
            )
