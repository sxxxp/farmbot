from database.connection import BaseRepository


class InventoryRepository(BaseRepository):

    async def get_inventory(self, user_id: int):
        """사용자 인벤토리 확인"""
        rows = await self.conn.fetch(
            """
            SELECT item_id, amount 
            FROM inventory
            WHERE user_id = $1;
        """,
            user_id,
        )
        return rows

    async def update_inventory(self, user_id: int, item_id: str, amount: int):
        """사용자 인벤토리 업데이트"""
        await self.conn.execute(
            """
            INSERT INTO inventory (user_id, item_id, amount) 
            VALUES ($1, $2, GREATEST($3,0))
            ON CONFLICT (user_id, item_id) DO UPDATE 
            SET amount = inventory.amount + $3
            WHERE inventory.amount + $3 >= 0;
            
        """,
            user_id,
            item_id,
            amount,
        )

    async def get_item(self, user_id: int, item_id: str):
        """사용자 아이템 확인"""
        row = await self.conn.fetchrow(
            """
            SELECT amount 
            FROM inventory
            WHERE user_id = $1 AND item_id = $2;
        """,
            user_id,
            item_id,
        )
        return row
