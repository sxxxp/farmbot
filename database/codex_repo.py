from database.connection import BaseRepository


class CodexRepository(BaseRepository):

    async def record(self, user_id: int, crop_id: str, variant: str, stars: int) -> bool:
        """수확 기록 (횟수 +1, 최고 별 갱신). 처음 등록이면 True"""
        return await self.conn.fetchval(
            """
            INSERT INTO user_codex (user_id, crop_id, variant, harvest_count, best_stars)
            VALUES ($1, $2, $3, 1, $4)
            ON CONFLICT (user_id, crop_id, variant) DO UPDATE
            SET harvest_count = user_codex.harvest_count + 1,
                best_stars = GREATEST(user_codex.best_stars, $4)
            RETURNING (xmax = 0);
            """,
            user_id,
            crop_id,
            variant,
            stars,
        )

    async def get_entries(self, user_id: int):
        return await self.conn.fetch(
            """
            SELECT crop_id, variant, harvest_count, best_stars
            FROM user_codex WHERE user_id = $1;
            """,
            user_id,
        )
