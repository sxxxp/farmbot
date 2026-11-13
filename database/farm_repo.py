import time
from typing import List

from database.connection import BaseRepository
from utils.dataenum import SlotStatus
from utils.datatype import FarmData


class FarmRepository(BaseRepository):

    async def check_farm(self, user_id: int):
        """사용자 밭 상태 확인"""
        rows: List[FarmData] = await self.conn.fetch(
            """
            SELECT slot_id, crop_id, planted_at, is_watered, fertilizer
            FROM user_farms 
            WHERE user_id = $1;
        """,
            user_id,
        )
        return rows

    async def ensure_slots(self, user_id: int, count: int) -> int:
        """1~count 번 땅이 없으면 빈 땅으로 만든다. 새로 만든 개수를 반환 (이미 있으면 0)"""
        result = await self.conn.execute(
            """
            INSERT INTO user_farms (user_id, slot_id)
            SELECT $1, s FROM generate_series(1, $2::int) AS s
            ON CONFLICT (user_id, slot_id) DO NOTHING;
        """,
            user_id,
            count,
        )
        # asyncpg 는 'INSERT 0 <n>' 형태의 상태 문자열을 돌려준다
        return int(result.split()[-1]) if result else 0

    async def check_farm_slot(self, user_id: int, slot_id: int):
        """사용자 밭 슬롯 상태 확인"""
        row: FarmData = await self.conn.fetchrow(
            """
            SELECT crop_id, planted_at, is_watered, fertilizer
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
                SET crop_id = $3, planted_at = $4, is_watered = FALSE, fertilizer = NULL;
            """,
            user_id,
            slot_id,
            crop_id,
            planted_at,
        )

    async def water_seed(self, user_id: int, slot_id: int) -> str | None:
        """물주기. 작물이 있고 아직 물을 안 줬을 때만 적용되고 작물 이름을 반환"""
        return await self.conn.fetchval(
            """
                UPDATE user_farms
                SET is_watered = TRUE
                WHERE user_id = $1 AND slot_id = $2
                  AND crop_id IS NOT NULL AND NOT is_watered
                RETURNING crop_id;
            """,
            user_id,
            slot_id,
        )

    async def harvest_crop(self, user_id: int, slot_id: int, now: int | None = None):
        """작물 수확. 다 자란 작물이 있으면 슬롯을 비우고 수확 직전 상태
        (crop_id, planted_at, is_watered, fertilizer) 를 돌려준다. 없으면 None"""
        now = int(time.time()) if now is None else now
        return await self.conn.fetchrow(
            """
                WITH old_data AS (
                    SELECT crop_id, planted_at, is_watered, fertilizer
                    FROM user_farms
                    WHERE user_id = $1 AND slot_id = $2
                      AND planted_at <= $3 AND crop_id IS NOT NULL
                    FOR UPDATE
                )
                UPDATE user_farms AS f
                SET crop_id = NULL, planted_at = NULL, is_watered = FALSE, fertilizer = NULL
                FROM old_data
                WHERE f.user_id = $1 AND f.slot_id = $2
                RETURNING old_data.crop_id, old_data.planted_at,
                          old_data.is_watered, old_data.fertilizer;
            """,
            user_id,
            slot_id,
            now,
        )

    async def apply_fertilizer(
        self, user_id: int, slot_id: int, item_id: str, reduce_seconds: int, now: int
    ) -> int | None:
        """비료 주기: 남은 성장 시간을 reduce_seconds 만큼 단축 (지금보다 앞당기지는 않음).
        작물이 자라는 중이고 이번 재배에 비료를 안 줬을 때만 적용, 새 수확 가능 시각 반환"""
        return await self.conn.fetchval(
            """
                UPDATE user_farms
                SET planted_at = GREATEST(planted_at - $4::bigint, $5::bigint),
                    fertilizer = $3
                WHERE user_id = $1 AND slot_id = $2
                  AND crop_id IS NOT NULL AND fertilizer IS NULL
                  AND planted_at > $5::bigint
                RETURNING planted_at;
            """,
            user_id,
            slot_id,
            item_id,
            reduce_seconds,
            now,
        )
