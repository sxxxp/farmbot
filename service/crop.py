from consistances import CROPS_DATA
from utils.datatype import CropData


def get_crop_data(crop_name: str) -> CropData:
    return CROPS_DATA.get(crop_name, {})
