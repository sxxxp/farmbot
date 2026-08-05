from typing import TypedDict


class CropData(TypedDict):
    grow_time: int
    buy_price: int
    sell_price: int
    buy_grade: int
    crop_type: str
    description: str
