from enum import Enum


class SlotStatus(Enum):
    LOCKED = "locked"  # 해금되지 않은 슬롯 (row 없음)
    EMPTY = "empty"  # 빈 슬롯 (crop_id 없음)
    PLANTED = "planted"  # 작물이 심어진 슬롯


class CropStatus(Enum):
    GROWING = "growing"  # 성장 중인 작물
    FULLY_GROWN = "fully_grown"  # 완전히 성장한 작물


class CropType(Enum):
    VEGETABLE = "vegetable"  # 채소
    FRUITING_VEGETABLE = "fruiting vegetable"  # 열매를 맺는 채소
    ROOT = "root"  # 뿌리채소
    FRUIT = "fruit"  # 과일
    GRAIN = "grain"  # 곡물
    LEGUME = "legume"  # 콩류
    HERB = "herb"  # 허브
    FLOWER = "flower"  # 꽃
