from database.connection import BaseRepository


class PassiveRepository(BaseRepository):

    async def get_ranks(self, user_id: int) -> dict[str, int]:
        rows = await self.conn.fetch(
            "SELECT node_id, rank FROM user_passives WHERE user_id = $1 AND rank > 0;",
            user_id,
        )
        return {row["node_id"]: row["rank"] for row in rows}

    async def add_rank(self, user_id: int, node_id: str, max_rank: int) -> int | None:
        """랭크 +1. 이미 최대면 None, 성공하면 새 랭크"""
        return await self.conn.fetchval(
            """
            INSERT INTO user_passives (user_id, node_id, rank) VALUES ($1, $2, 1)
            ON CONFLICT (user_id, node_id) DO UPDATE
            SET rank = user_passives.rank + 1
            WHERE user_passives.rank < $3
            RETURNING rank;
            """,
            user_id,
            node_id,
            max_rank,
        )

    async def reset(self, user_id: int):
        await self.conn.execute("DELETE FROM user_passives WHERE user_id = $1;", user_id)

    async def lock_user(self, user_id: int):
        """포인트 계산 중 동시 요청 방지용 행 잠금. (farm_exp, gold) 반환"""
        return await self.conn.fetchrow(
            "SELECT farm_exp, gold FROM users WHERE user_id = $1 FOR UPDATE;", user_id
        )
