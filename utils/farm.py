from time import time

from database.connection import db
from database.farm_repo import FarmRepository
from utils.crop import get_crop_data
from utils.dataenum import SlotStatus


async def plant_seed(uid: int, slot_id: int, crop_name: str):
    if not slot_vaild(uid, slot_id):
        return None
    data = get_crop_data(crop_name)
    grow_time = data.get("grow_time")
    async with db.pool.acquire() as conn:
        FarmRepo = FarmRepository(conn)
        await FarmRepo.plant_seed(uid, slot_id, crop_name, int(time() + grow_time))


async def slot_vaild(uid: int, slot_id: int) -> bool:
    async with db.pool.acquire() as conn:
        FarmRepo = FarmRepository(conn)
        state, _ = await FarmRepo.check_farm_status(uid, slot_id)
        return state != SlotStatus.LOCKED
    return False


async def slot_empty(uid: int, slot_id: int) -> bool:
    async with db.pool.acquire() as conn:
        FarmRepo = FarmRepository(conn)
        state, _ = await FarmRepo.check_farm_status(uid, slot_id)
        return state == SlotStatus.EMPTY


async def harvest_crop(uid: int, slot_id: int):
    async with db.pool.acquire() as conn:
        FarmRepo = FarmRepository(conn)
        await FarmRepo.harvest_crop(uid, slot_id)
        return True
    return False
