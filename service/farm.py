from time import time
from typing import List
from database.inventory_repo import InventoryRepository
from database.user_repo import UserRepository
from service.base import BaseService
from database.farm_repo import FarmRepository
from service.crop import get_crop_data
from utils.dataenum import SlotStatus
from utils.datatype import FarmData


class FarmService(BaseService):

    async def plant_seed(self, user_id: int, slot_id: int, crop_name: str):
        import traceback

        try:
            if not await self.slot_vaild(user_id, slot_id):
                return False

            data = get_crop_data(crop_name)
            if not data:
                print(f"[Error] {crop_name} 데이터가 존재하지 않습니다.")
                return False

            grow_time = data.get("grow_time", 0)

            async with self.db.transaction() as conn:
                FarmRepo = FarmRepository(conn)
                InventoryRepo = InventoryRepository(conn)

                await InventoryRepo.update_inventory(user_id, crop_name + "_seed", -1)
                await FarmRepo.plant_seed(
                    user_id, slot_id, crop_name, int(time() + grow_time)
                )
                return True

        except Exception as e:
            print(f"[plant_seed 에러 발생]: {e}")
            traceback.print_exc()  # 콘솔에 정확한 트레이스백 출력
            return False

    async def slot_vaild(self, user_id: int, slot_id: int) -> bool:
        async with self.db.connection() as conn:
            FarmRepo = FarmRepository(conn)
            state, _ = await FarmRepo.check_farm_slot(user_id, slot_id)
            return state != SlotStatus.LOCKED
        return False

    async def slot_empty(self, user_id: int, slot_id: int) -> bool:
        async with self.db.connection() as conn:
            FarmRepo = FarmRepository(conn)
            state, _ = await FarmRepo.check_farm_slot(user_id, slot_id)
            return state == SlotStatus.EMPTY

    async def get_farm(self, user_id: int) -> List[FarmData]:
        async with self.db.connection() as conn:
            farmRepo = FarmRepository(conn)
            return await farmRepo.check_farm(user_id)

    async def harvest_crop(self, user_id: int, slot_id: int):
        import traceback

        try:
            async with self.db.transaction() as conn:
                FarmRepo = FarmRepository(conn)
                cropId = await FarmRepo.harvest_crop(user_id, slot_id)
                if cropId:
                    InventoryRepo = InventoryRepository(conn)
                    UserRepo = UserRepository(conn)
                    crop = get_crop_data(cropId)
                    await InventoryRepo.update_inventory(user_id, cropId, 1)
                    await UserRepo.update_user_exp(user_id, crop["exp"])

                return cropId
            return False
        except Exception as e:
            traceback.print_exc()
            return False

    async def water_seed(self, user_id: int, slot_id: int):
        async with self.db.transaction() as conn:
            farmRepo = FarmRepository(conn)
            await farmRepo.water_seed(user_id, slot_id)
            return True
        return False
