import math

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

    async def create_user(self, user_id: int) -> UserData:
        async with self.db.connection() as conn:
            userRepo = UserRepository(conn)
            await userRepo.create_user(user_id)
