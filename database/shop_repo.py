from database.connection import BaseRepository


class ShopRepository(BaseRepository):

    async def get_items(self, user_id: int, period: str):
        """이번 입고 기간의 진열 목록"""
        return await self.conn.fetch(
            """
            SELECT slot, item_id, price, stock, bought
            FROM user_shop
            WHERE user_id = $1 AND period = $2
            ORDER BY slot;
            """,
            user_id,
            period,
        )

    async def restock(self, user_id: int, period: str, items: list[dict]):
        """새 입고: 지난 기간 진열을 지우고 새 품목을 넣는다"""
        await self.conn.execute(
            "DELETE FROM user_shop WHERE user_id = $1 AND period <> $2;",
            user_id,
            period,
        )
        await self.conn.executemany(
            """
            INSERT INTO user_shop (user_id, slot, period, item_id, price, stock)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (user_id, slot) DO NOTHING;
            """,
            [
                (user_id, item["slot"], period, item["item_id"], item["price"], item["stock"])
                for item in items
            ],
        )

    async def buy(self, user_id: int, slot: int, period: str, amount: int):
        """재고 차감. 재고가 부족하거나 기간이 지났으면 None, 성공하면 (item_id, price)"""
        return await self.conn.fetchrow(
            """
            UPDATE user_shop SET bought = bought + $4
            WHERE user_id = $1 AND slot = $2 AND period = $3
              AND stock - bought >= $4
            RETURNING item_id, price;
            """,
            user_id,
            slot,
            period,
            amount,
        )
