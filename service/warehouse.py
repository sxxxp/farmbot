from consistances import CROPS_DATA, FERTILIZERS
from database.inventory_repo import InventoryRepository
from database.user_repo import UserRepository
from service.base import BaseService
from service.passive import get_effects
from service.produce import ROTTEN_ITEM, ROTTEN_NAME, parse_produce_id, produce_price
from utils.datatype import ProduceItem, SellResult, WarehouseBoard


def build_warehouse(rows, gold: int, sell_bonus_pct: float = 0) -> WarehouseBoard:
    """인벤토리 행을 수확물 / 씨앗 / 비료로 분류. 수확물 가격은 판매가 보너스 적용"""
    produce: list[ProduceItem] = []
    seeds: list[tuple[str, int]] = []
    fertilizers: list[tuple[str, int]] = []

    for row in rows:
        item_id, amount = row["item_id"], row["amount"]
        if amount <= 0:
            continue
        if item_id in FERTILIZERS:
            fertilizers.append((item_id, amount))
        elif item_id.endswith("_seed") and item_id.removesuffix("_seed") in CROPS_DATA:
            seeds.append((item_id.removesuffix("_seed"), amount))
        elif item_id == ROTTEN_ITEM:
            produce.append(
                ProduceItem(
                    item_id=item_id,
                    crop=ROTTEN_NAME,
                    stars=0,
                    variant=None,
                    amount=amount,
                    price=produce_price(item_id),
                )
            )
        elif (parsed := parse_produce_id(item_id)) is not None:
            crop, stars, variant = parsed
            produce.append(
                ProduceItem(
                    item_id=item_id,
                    crop=crop,
                    stars=stars,
                    variant=variant,
                    amount=amount,
                    price=produce_price(item_id, sell_bonus_pct),
                )
            )

    produce.sort(key=lambda p: (-p["price"], p["crop"], -p["stars"]))
    seeds.sort(key=lambda s: (CROPS_DATA[s[0]]["buy_grade"], s[0]))
    fertilizers.sort(key=lambda f: (FERTILIZERS[f[0]]["grade"], f[0]))
    return WarehouseBoard(
        gold=gold, produce=produce, seeds=seeds, fertilizers=fertilizers
    )


class WarehouseService(BaseService):

    async def get_warehouse(self, user_id: int) -> WarehouseBoard:
        async with self.db.connection() as conn:
            user = await UserRepository(conn).get_user(user_id)
            rows = await InventoryRepository(conn).get_inventory(user_id)
            effects = await get_effects(conn, user_id)
        return build_warehouse(rows, user["gold"], effects.get("sell_price_pct", 0))

    async def sell(self, user_id: int, item_id: str, amount: int) -> SellResult:
        """수확물 판매. 판매가는 item_id(별/변종)와 패시브로 서버에서 계산"""
        if amount < 1 or produce_price(item_id) <= 0:
            return SellResult(ok=False, sold=0, earned=0)

        async with self.db.transaction() as conn:
            effects = await get_effects(conn, user_id)
            price = produce_price(item_id, effects.get("sell_price_pct", 0))
            if not await InventoryRepository(conn).remove_item(user_id, item_id, amount):
                return SellResult(ok=False, sold=0, earned=0)
            earned = price * amount
            await UserRepository(conn).update_user_gold(user_id, earned)
        return SellResult(ok=True, sold=amount, earned=earned)

    async def sell_all(self, user_id: int) -> SellResult:
        """보유한 수확물 전부 판매 (씨앗/비료는 제외)"""
        async with self.db.transaction() as conn:
            inventory = InventoryRepository(conn)
            bonus = (await get_effects(conn, user_id)).get("sell_price_pct", 0)
            sold = earned = 0
            for row in await inventory.get_inventory(user_id):
                item_id, amount = row["item_id"], row["amount"]
                price = produce_price(item_id, bonus)
                if amount <= 0 or price <= 0:
                    continue
                if await inventory.remove_item(user_id, item_id, amount):
                    sold += amount
                    earned += price * amount
            if earned:
                await UserRepository(conn).update_user_gold(user_id, earned)
        return SellResult(ok=sold > 0, sold=sold, earned=earned)
