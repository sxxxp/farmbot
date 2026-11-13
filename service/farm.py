from time import time
from typing import List
from consistances import FARM_CONFIG, FERTILIZERS
from database.codex_repo import CodexRepository
from database.inventory_repo import InventoryRepository
from database.user_repo import UserRepository
from service.base import BaseService
from database.farm_repo import FarmRepository
from service.crop import get_crop_data
from service.codex import codex_variant, in_codex
from service.passive import get_effects
from service.produce import (
    GOLDEN,
    MUTANT,
    ROTTEN_ITEM,
    is_rotten,
    make_produce_id,
    roll_quality,
    star_exp,
)
from service.user import UserService
from service.quest import record_quest_progress
from utils.dataenum import SlotStatus
from utils.datatype import FarmData, HarvestResult


def slots_for_level(level: int) -> int:
    """레벨별 땅 개수 (data/farm.json slots_by_level 중 레벨 이하인 마지막 행)"""
    slots = 1
    for row in FARM_CONFIG["slots_by_level"]:
        if row["min_level"] <= level:
            slots = row["slots"]
    return slots


def next_slot_level(level: int) -> int | None:
    """다음 땅이 열리는 레벨 (더 없으면 None)"""
    current = slots_for_level(level)
    for row in FARM_CONFIG["slots_by_level"]:
        if row["slots"] > current:
            return row["min_level"]
    return None


def reduced_grow_time(grow_time: int, grow_time_pct: float) -> int:
    """성장 시간 단축 % 적용 (최소 1초)"""
    return max(1, int(round(grow_time * (1 - grow_time_pct / 100))))


def bonus_exp(exp: int, exp_pct: float) -> int:
    return int(round(exp * (1 + exp_pct / 100)))


class FarmService(BaseService):

    async def plant_seed(self, user_id: int, slot_id: int, crop_name: str):
        import traceback

        try:
            if not await self.slot_empty(user_id, slot_id):
                return False

            data = get_crop_data(crop_name)
            if not data:
                print(f"[Error] {crop_name} 데이터가 존재하지 않습니다.")
                return False

            grow_time = data.get("grow_time", 0)

            async with self.db.transaction() as conn:
                FarmRepo = FarmRepository(conn)
                InventoryRepo = InventoryRepository(conn)

                seed = await InventoryRepo.get_item(user_id, crop_name + "_seed")
                if not seed or seed["amount"] < 1:
                    return False

                await InventoryRepo.update_inventory(user_id, crop_name + "_seed", -1)
                # 성장 시간 단축 (도감 보상 등)
                effects = await get_effects(conn, user_id)
                grow_time = reduced_grow_time(grow_time, effects.get("grow_time_pct", 0))
                await FarmRepo.plant_seed(
                    user_id, slot_id, crop_name, int(time() + grow_time)
                )
                await record_quest_progress(conn, user_id, "plant", crop_name)
                return True

        except Exception as e:
            print(f"[plant_seed 에러 발생]: {e}")
            traceback.print_exc()  # 콘솔에 정확한 트레이스백 출력
            return False

    async def get_seeds(self, user_id: int) -> List[tuple[str, int]]:
        """보유한 씨앗 목록 (작물 이름, 개수)"""
        async with self.db.connection() as conn:
            InventoryRepo = InventoryRepository(conn)
            rows = await InventoryRepo.get_inventory(user_id)
        seeds = []
        for row in rows:
            item_id, amount = row["item_id"], row["amount"]
            if not item_id.endswith("_seed") or amount < 1:
                continue
            crop_name = item_id[: -len("_seed")]
            if get_crop_data(crop_name):
                seeds.append((crop_name, amount))
        return seeds

    async def get_fertilizers(self, user_id: int) -> List[tuple[str, int]]:
        """보유한 비료 목록 (item_id, 개수), 등급 낮은 순"""
        async with self.db.connection() as conn:
            rows = await InventoryRepository(conn).get_inventory(user_id)
        owned = [
            (row["item_id"], row["amount"])
            for row in rows
            if row["item_id"] in FERTILIZERS and row["amount"] > 0
        ]
        return sorted(owned, key=lambda item: FERTILIZERS[item[0]]["grade"])

    async def apply_fertilizer(
        self, user_id: int, slot_id: int, item_id: str
    ) -> int | None:
        """비료 주기. 성공하면 새 수확 가능 시각, 실패하면 None
        (비료 없음 / 빈 땅 / 이미 다 자람 / 이번 재배에 이미 비료 사용)"""
        fertilizer = FERTILIZERS.get(item_id)
        if not fertilizer:
            return None

        async with self.db.transaction() as conn:
            inventory = InventoryRepository(conn)
            owned = await inventory.get_item(user_id, item_id)
            if not owned or owned["amount"] < 1:
                return None

            ready_at = await FarmRepository(conn).apply_fertilizer(
                user_id,
                slot_id,
                item_id,
                fertilizer["reduce_minutes"] * 60,
                int(time()),
            )
            if ready_at is None:
                return None

            await inventory.update_inventory(user_id, item_id, -1)
            return ready_at

    async def slot_vaild(self, user_id: int, slot_id: int) -> bool:
        async with self.db.connection() as conn:
            FarmRepo = FarmRepository(conn)
            state, _ = await FarmRepo.check_farm_slot(user_id, slot_id)
            return state != SlotStatus.LOCKED
        return False

    async def slot_empty(self, user_id: int, slot_id: int) -> bool:
        async with self.db.connection() as conn:
            FarmRepo = FarmRepository(conn)
            state, _ = await FarmRepo.check_farm_slot(user_id, slot_id)
            return state == SlotStatus.EMPTY

    async def get_farm(self, user_id: int) -> List[FarmData]:
        """농장 조회. 레벨에 맞는 땅이 아직 없으면 먼저 만들어 준다
        (레벨이 어떤 경로로 올랐든, 기존 유저든 농장을 열 때 맞춰진다)"""
        async with self.db.transaction() as conn:
            farmRepo = FarmRepository(conn)
            exp = await UserRepository(conn).get_user_exp(user_id)
            level = UserService.calculate_level(exp)
            await farmRepo.ensure_slots(user_id, slots_for_level(level))
            return await farmRepo.check_farm(user_id)

    async def harvest_crop(self, user_id: int, slot_id: int) -> HarvestResult | None:
        """수확. 물·비료·수확 타이밍·작물 난이도·패시브에 따라 별(1~5)과
        변종(돌연변이/황금)이 정해진다. 수확할 작물이 없거나 덜 자랐으면 None"""
        import traceback

        try:
            async with self.db.transaction() as conn:
                now = int(time())
                row = await FarmRepository(conn).harvest_crop(user_id, slot_id, now)
                if not row:
                    return None

                crop_id = row["crop_id"]
                crop = get_crop_data(crop_id)

                # 너무 오래 방치: 작물 대신 썩은 식물. 경험치/퀘스트/도감 없음
                if is_rotten(crop_id, row["planted_at"], now):
                    await InventoryRepository(conn).update_inventory(
                        user_id, ROTTEN_ITEM, 1
                    )
                    return HarvestResult(
                        crop=crop_id,
                        stars=0,
                        variant=None,
                        mutant=False,
                        golden=False,
                        item_id=ROTTEN_ITEM,
                        level_up=None,
                        new_codex=False,
                        rotten=True,
                        exp=0,
                    )

                effects = await get_effects(conn, user_id)
                stars, variant = roll_quality(
                    crop_id,
                    row["is_watered"],
                    row["fertilizer"],
                    row["planted_at"],
                    now,
                    effects=effects,
                )
                item_id = make_produce_id(crop_id, stars, variant)

                users = UserRepository(conn)
                exp_before = await users.get_user_exp(user_id)
                # 별이 높을수록 경험치 배율 ↑, 그 위에 패시브/도감 경험치 보너스
                exp = bonus_exp(star_exp(crop["exp"], stars), effects.get("exp_pct", 0))
                await InventoryRepository(conn).update_inventory(user_id, item_id, 1)
                await users.update_user_exp(user_id, exp)
                await record_quest_progress(
                    conn, user_id, "harvest", crop_id, stars=stars
                )
                # 이벤트 작물은 도감 대상이 아니다
                new_codex = in_codex(crop_id) and await CodexRepository(conn).record(
                    user_id, crop_id, codex_variant(variant), stars
                )

                level_before = UserService.calculate_level(exp_before)
                level_after = UserService.calculate_level(exp_before + exp)
                return HarvestResult(
                    crop=crop_id,
                    stars=stars,
                    variant=variant,
                    mutant=variant == MUTANT,
                    golden=variant == GOLDEN,
                    item_id=item_id,
                    level_up=level_after if level_after > level_before else None,
                    new_codex=bool(new_codex),
                    rotten=False,
                    exp=exp,
                )
        except Exception:
            traceback.print_exc()
            return None

    async def water_seed(self, user_id: int, slot_id: int) -> bool:
        async with self.db.transaction() as conn:
            farmRepo = FarmRepository(conn)
            crop_id = await farmRepo.water_seed(user_id, slot_id)
            if not crop_id:
                return False
            await record_quest_progress(conn, user_id, "water", crop_id)
            return True
