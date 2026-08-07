from typing import TypedDict


class CropData(TypedDict):
    grow_time: int
    buy_price: int
    sell_price: int
    buy_grade: int
    crop_type: str
    exp: int
    description: str


class UserData(TypedDict):
    user_id: int
    farm_exp: int
    level: int
    gold: int


class FarmData(TypedDict):
    slot_id: int
    crop_id: int | None
    planted_at: int | None
    is_watered: bool | None
