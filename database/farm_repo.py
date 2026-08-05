import time

from database.connection import BaseRepository
from database.inventory_repo import InventoryRepository
from utils.dataenum import SlotStatus


class FarmRepository(BaseRepository):

    async def check_farm(self, user_id: int):
        """사용자 밭 상태 확인"""
        rows = await self.db.fetch(
            """
            SELECT slot_id, crop_id, planted_at, is_watered 
            FROM user_farms 
            WHERE user_id = $1;
        """,
            user_id,
        )
        return rows

    async def check_farm_slot(self, user_id: int, slot_id: int):
        """사용자 밭 슬롯 상태 확인"""
        row = await self.db.fetchrow(
            """
            SELECT crop_id, planted_at, is_watered 
            FROM user_farms 
            WHERE user_id = $1 AND slot_id = $2;
        """,
            user_id,
            slot_id,
        )
        if not row:
            return SlotStatus.LOCKED, None

        if row["crop_id"] is None:
            return SlotStatus.EMPTY, row

        return SlotStatus.PLANTED, row

    async def plant_seed(
        self, user_id: int, slot_id: int, crop_id: str, planted_at: int
    ):
        """작물 심기"""
        inventory_repo = InventoryRepository(self.db)
        await inventory_repo.update_inventory(user_id, crop_id + "_seed", -1)
        await self.db.execute(
            """
            INSERT INTO user_farms (user_id, slot_id, crop_id, planted_at) 
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (user_id, slot_id) DO UPDATE 
            SET crop_id = $3, planted_at = $4, is_watered = FALSE;
        """,
            user_id,
            slot_id,
            crop_id,
            planted_at,
        )

    async def harvest_crop(self, user_id: int, slot_id: int):
        """작물 수확"""
        if await self.crop_harvestable(user_id, slot_id):
            await self.db.execute(
                """
                UPDATE user_farms 
                SET crop_id = NULL, planted_at = NULL, is_watered = FALSE 
                WHERE user_id = $1 AND slot_id = $2;
            """,
                user_id,
                slot_id,
            )
            InventoryRepo = InventoryRepository(self.db)
            await InventoryRepo.update_inventory(
                user_id, await self.get_crop_id(user_id, slot_id), 1
            )

    async def crop_harvestable(self, user_id: int, slot_id: int) -> bool:
        """작물 수확 가능 여부 확인"""
        row = await self.db.fetchrow(
            """
            SELECT crop_id, planted_at 
            FROM user_farms 
            WHERE user_id = $1 AND slot_id = $2;
        """,
            user_id,
            slot_id,
        )
        if not row or row["crop_id"] is None:
            return False

        current_time = int(time())
        return current_time >= row["planted_at"]
