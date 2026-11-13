"""제작: 수확물을 재료로 비료/씨앗 등을 만든다 (data/recipes.json).

재료 조건(spec): crop(작물 이름) / crop_type(작물 종류) / min_stars(별 이상) /
variant("mutant"·"golden") / count. 지정한 조건만 확인한다.
- variant 를 지정하지 않은 재료에는 돌연변이·황금 작물을 쓰지 않는다 (귀한 작물 보호)
- 조건에 맞는 수확물 중 판매가가 낮은 것부터 소모한다 (같으면 별이 적은 것부터)
패시브: craft_material_pct(재료 -%), craft_gold_pct(골드 비용 -%)
"""

from consistances import CROPS_DATA, RECIPE_INDEX, RECIPES
from database.inventory_repo import InventoryRepository
from database.passive_repo import PassiveRepository
from database.user_repo import UserRepository
from service.base import BaseService
from service.passive import get_effects
from service.produce import parse_produce_id, produce_price
from service.user import UserService
from utils.datatype import CraftBoard, CraftIngredientStatus, CraftRecipeStatus

MAX_CRAFT_TIMES = 99


class CraftFailed(Exception):
    """트랜잭션 롤백용"""

    def __init__(self, reason: str):
        self.reason = reason


def reduced_count(count: int, material_pct: float) -> int:
    """재료 감소 적용 (반올림, 최소 1개)"""
    return max(1, count - int(count * material_pct / 100 + 0.5))


def reduced_gold(gold: int, gold_pct: float) -> int:
    return max(0, int(round(gold * (1 - gold_pct / 100))))


def matches(spec: dict, item_id: str) -> bool:
    parsed = parse_produce_id(item_id)
    if parsed is None:
        return False
    crop, stars, variant = parsed
    if spec.get("crop") and spec["crop"] != crop:
        return False
    if spec.get("crop_type") and CROPS_DATA[crop]["crop_type"] != spec["crop_type"]:
        return False
    if spec.get("min_stars") and stars < spec["min_stars"]:
        return False
    return variant == spec.get("variant")  # 둘 다 None 이면 일반 작물만


def plan_consumption(
    recipe: dict, stock: dict[str, int], material_pct: float
) -> dict[str, int] | None:
    """1회 제작에 소모할 수확물 {item_id: 개수}. 재료가 모자라면 None.
    stock 은 수정하지 않는다."""
    remaining = dict(stock)
    used: dict[str, int] = {}
    for spec in recipe["ingredients"]:
        need = reduced_count(spec["count"], material_pct)
        candidates = sorted(
            (i for i, a in remaining.items() if a > 0 and matches(spec, i)),
            key=lambda i: (produce_price(i), parse_produce_id(i)[1], i),
        )
        for item_id in candidates:
            take = min(need, remaining[item_id])
            remaining[item_id] -= take
            used[item_id] = used.get(item_id, 0) + take
            need -= take
            if need == 0:
                break
        if need > 0:
            return None
    return used


def plan_many(
    recipe: dict, stock: dict[str, int], material_pct: float, times: int
) -> dict[str, int] | None:
    remaining = dict(stock)
    total: dict[str, int] = {}
    for _ in range(times):
        used = plan_consumption(recipe, remaining, material_pct)
        if used is None:
            return None
        for item_id, amount in used.items():
            remaining[item_id] -= amount
            total[item_id] = total.get(item_id, 0) + amount
    return total


def max_crafts(recipe: dict, stock: dict[str, int], material_pct: float) -> int:
    remaining = dict(stock)
    count = 0
    while count < MAX_CRAFT_TIMES:
        used = plan_consumption(recipe, remaining, material_pct)
        if used is None:
            break
        for item_id, amount in used.items():
            remaining[item_id] -= amount
        count += 1
    return count


def produce_stock(rows) -> dict[str, int]:
    return {
        row["item_id"]: row["amount"]
        for row in rows
        if row["amount"] > 0 and parse_produce_id(row["item_id"]) is not None
    }


def build_board(level: int, gold: int, rows, effects: dict) -> CraftBoard:
    stock = produce_stock(rows)
    material_pct = effects.get("craft_material_pct", 0)
    gold_pct = effects.get("craft_gold_pct", 0)
    recipes: list[CraftRecipeStatus] = []
    for recipe in RECIPES:
        cost = reduced_gold(recipe["gold"], gold_pct)
        ingredients = [
            CraftIngredientStatus(
                spec=spec,
                need=reduced_count(spec["count"], material_pct),
                have=sum(a for i, a in stock.items() if matches(spec, i)),
            )
            for spec in recipe["ingredients"]
        ]
        locked = recipe["min_level"] > level
        craftable = 0 if locked else max_crafts(recipe, stock, material_pct)
        if cost > 0:
            craftable = min(craftable, gold // cost)
        recipes.append(
            CraftRecipeStatus(
                recipe=recipe,
                locked=locked,
                gold=cost,
                ingredients=ingredients,
                max_crafts=craftable,
            )
        )
    return CraftBoard(level=level, gold=gold, material_pct=material_pct, recipes=recipes)


class CraftService(BaseService):

    async def get_board(self, user_id: int) -> CraftBoard:
        async with self.db.connection() as conn:
            user = await UserRepository(conn).get_user(user_id)
            rows = await InventoryRepository(conn).get_inventory(user_id)
            effects = await get_effects(conn, user_id)
        level = UserService.calculate_level(user["farm_exp"])
        return build_board(level, user["gold"], rows, effects)

    async def craft(self, user_id: int, recipe_id: str, times: int = 1) -> dict:
        """제작. {'ok', 'crafted', 'reason', 'item', 'count', 'gold'}
        reason: 'unknown' / 'invalid' / 'locked' / 'no_materials' / 'no_gold'"""
        recipe = RECIPE_INDEX.get(recipe_id)
        result = {"ok": False, "crafted": 0, "reason": None, "item": None, "count": 0, "gold": 0}
        if recipe is None:
            return {**result, "reason": "unknown"}
        if not 1 <= times <= MAX_CRAFT_TIMES:
            return {**result, "reason": "invalid"}

        try:
            async with self.db.transaction() as conn:
                user = await PassiveRepository(conn).lock_user(user_id)  # 동시 제작 방지
                if recipe["min_level"] > UserService.calculate_level(user["farm_exp"]):
                    raise CraftFailed("locked")

                effects = await get_effects(conn, user_id)
                inventory = InventoryRepository(conn)
                stock = produce_stock(await inventory.get_inventory(user_id))
                used = plan_many(
                    recipe, stock, effects.get("craft_material_pct", 0), times
                )
                if used is None:
                    raise CraftFailed("no_materials")

                gold = reduced_gold(recipe["gold"], effects.get("craft_gold_pct", 0)) * times
                if gold and await UserRepository(conn).spend_gold(user_id, gold) is None:
                    raise CraftFailed("no_gold")

                for item_id, amount in used.items():
                    if not await inventory.remove_item(user_id, item_id, amount):
                        raise CraftFailed("no_materials")

                output = recipe["output"]
                count = output["count"] * times
                await inventory.update_inventory(user_id, output["item"], count)
                return {
                    **result,
                    "ok": True,
                    "crafted": times,
                    "item": output["item"],
                    "count": count,
                    "gold": gold,
                }
        except CraftFailed as e:
            return {**result, "reason": e.reason}
