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

    @staticmethod
    def level_exp_range(level: int) -> tuple[int, int]:
        """해당 레벨이 시작되는 누적 경험치와 다음 레벨이 되는 누적 경험치
        (calculate_level 의 역함수: 레벨 L 은 100 * (L-1)^2 부터)"""
        return 100 * (level - 1) ** 2, 100 * level**2

    async def get_user(self, user_id: int) -> UserData:
        async with self.db.connection() as conn:
            userRepo = UserRepository(conn)
            row = await userRepo.get_user(user_id)
            if row is None:
                return None
            # asyncpg Record 는 수정할 수 없으므로 dict 로 변환
            userData = dict(row)
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
