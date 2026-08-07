import math
from database.inventory_repo import InventoryRepository
from database.user_repo import UserRepository
from service.base import BaseService
from utils.datatype import UserData


class UserService(BaseService):

    @staticmethod
    def calculate_level(exp: int) -> int:
        if exp < 0:
            return 1
        return math.floor(math.sqrt(exp / 100)) + 1

    async def get_user(self, user_id: int) -> UserData:
        async with self.db.connection() as conn:
            userRepo = UserRepository(conn)
            userData = await userRepo.get_user(user_id)
            userData["level"] = self.calculate_level(userData["farm_exp"])
            return userData

    async def is_user_exist(self, user_id: int) -> bool:
        async with self.db.connection() as conn:
            userRepo = UserRepository(conn)
            UserData = await userRepo.get_user(user_id)
            return True if UserData else False

    async def create_user(self, user_id: int) -> UserData:
        async with self.db.connection() as conn:
            userRepo = UserRepository(conn)
            user = await userRepo.create_user(user_id)
            return user
        return None

    async def give_item(self, user_id: int, item_id: str, amount: int):
        async with self.db.connection() as conn:
            invenRepo = InventoryRepository(conn)
            await invenRepo.update_inventory(user_id, item_id, amount)
            return True
        return False
