import time
from typing import List

from database.connection import BaseRepository
from database.inventory_repo import InventoryRepository
from utils.dataenum import SlotStatus
from utils.datatype import FarmData


class FarmRepository(BaseRepository):

    async def check_farm(self, user_id: int):
        """사용자 밭 상태 확인"""
        rows: List[FarmData] = await self.conn.fetch(
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
        row: FarmData = await self.conn.fetchrow(
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
        """씨앗 심기"""
        await self.conn.execute(
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

    async def water_seed(self, user_id: int, slot_id: int):
        await self.conn.execute(
            """
                UPDATE user_farms
                SET is_watered = TRUE
                WHERE user_id = $1 AND slot_id = $2;
            """,
            user_id,
            slot_id,
        )

    async def harvest_crop(self, user_id: int, slot_id: int):
        """작물 수확"""
        crop_id = await self.conn.fetchrow(
            """
                WITH old_data AS (
                    SELECT crop_id FROM user_farms 
                    WHERE user_id = $1 AND slot_id = $2 AND planted_at <= $3 AND crop_id IS NOT NULL
                )
                UPDATE user_farms 
                SET crop_id = NULL, planted_at = NULL, is_watered = FALSE 
                WHERE user_id = $1 AND slot_id = $2 AND EXISTS (SELECT 1 FROM old_data)
                RETURNING (SELECT crop_id FROM old_data);
            """,
            user_id,
            slot_id,
            int(time.time()),
        )
        return crop_id["crop_id"]
