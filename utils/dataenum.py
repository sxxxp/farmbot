from enum import Enum


class SlotStatus(Enum):
    LOCKED = "locked"  # 해금되지 않은 슬롯 (row 없음)
    EMPTY = "empty"  # 빈 슬롯 (crop_id 없음)
    PLANTED = "planted"  # 작물이 심어진 슬롯


class CropStatus(Enum):
    GROWING = "growing"  # 성장 중인 작물
    FULLY_GROWN = "fully_grown"  # 완전히 성장한 작물


class CropType(Enum):
    채소 = "vegetable"  # 채소
    과채류 = "fruiting vegetable"  # 열매를 맺는 채소
    뿌리채소 = "root"  # 뿌리채소
    과일 = "fruit"  # 과일
    곡물 = "grain"  # 곡물
    콩류 = "legume"  # 콩류
    허브 = "herb"  # 허브
    꽃 = "flower"  # 꽃
