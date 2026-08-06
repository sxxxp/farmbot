from time import time
from database.inventory_repo import InventoryRepository
from database.user_repo import UserRepository
from service.base import BaseService
from database.farm_repo import FarmRepository
from service.crop import get_crop_data
from utils.dataenum import SlotStatus


class FarmService(BaseService):

    async def plant_seed(self, uid: int, slot_id: int, crop_name: str):
        if not await self.slot_vaild(uid, slot_id):
            return None
        data = get_crop_data(crop_name)
        grow_time = data.get("grow_time")
        async with self.db.transaction() as conn:
            FarmRepo = FarmRepository(conn)
            InventoryRepo = InventoryRepository(conn)
            await InventoryRepo.update_inventory(uid, crop_name + "_seed", -1)
            await FarmRepo.plant_seed(uid, slot_id, crop_name, int(time() + grow_time))

    async def slot_vaild(self, uid: int, slot_id: int) -> bool:
        async with self.db.connection() as conn:
            FarmRepo = FarmRepository(conn)
            state, _ = await FarmRepo.check_farm_status(uid, slot_id)
            return state != SlotStatus.LOCKED
        return False

    async def slot_empty(self, uid: int, slot_id: int) -> bool:
        async with self.db.connection() as conn:
            FarmRepo = FarmRepository(conn)
            state, _ = await FarmRepo.check_farm_status(uid, slot_id)
            return state == SlotStatus.EMPTY

    async def harvest_crop(self, uid: int, slot_id: int):
        async with self.db.transaction() as conn:
            FarmRepo = FarmRepository(conn)
            InventoryRepo = InventoryRepository(conn)
            UserRepo = UserRepository(conn)
            cropId = await FarmRepo.harvest_crop(uid, slot_id)
            crop = get_crop_data(cropId)
            await InventoryRepo.update_inventory(uid, cropId, 1)
            await UserRepo.update_user_exp(uid, crop["exp"])

            return True
        return False
