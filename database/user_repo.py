from database.connection import BaseRepository
from utils.datatype import UserData


class UserRepository(BaseRepository):

    async def create_user(self, user_id: int):
        """사용자 생성"""
        user: UserData = await self.conn.fetchrow(
            """
            INSERT INTO users (user_id) VALUES ($1)
            ON CONFLICT (user_id) DO NOTHING
            RETURNING *;
            """,
            user_id,
        )
        return user

    async def get_user(self, user_id: int):
        """사용자 정보 조회"""
        row = await self.conn.fetchrow(
            """
            SELECT * FROM users WHERE user_id = $1;
            """,
            user_id,
        )
        return row

    async def update_user_gold(self, user_id: int, gold: int):
        """사용자 골드 업데이트"""
        await self.conn.execute(
            """
            UPDATE users SET gold = gold + $1 WHERE user_id = $2;
            """,
            gold,
            user_id,
        )

    async def spend_gold(self, user_id: int, amount: int) -> int | None:
        """골드 차감. 잔액이 부족하면 아무것도 바꾸지 않고 None, 성공하면 남은 골드"""
        return await self.conn.fetchval(
            """
            UPDATE users SET gold = gold - $2
            WHERE user_id = $1 AND gold >= $2
            RETURNING gold;
            """,
            user_id,
            amount,
        )

    async def get_user_exp(self, user_id: int):
        """사용자 레벨 조회"""

        row: UserData = await self.conn.fetchrow(
            """
            SELECT farm_exp FROM users WHERE user_id = $1;
            """,
            user_id,
        )
        return row["farm_exp"] if row and row["farm_exp"] is not None else 1

    async def update_user_exp(self, user_id: int, exp: int):
        """사용자 경험치 업데이트"""
        await self.conn.execute(
            """
            UPDATE users SET farm_exp = farm_exp + $1 WHERE user_id = $2;
            """,
            exp,
            user_id,
        )
