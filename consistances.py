import json
from discord import app_commands
from utils.datatype import CropData


def load_crops_data() -> dict[str, CropData]:
    with open("data/crops.json", "r", encoding="utf-8") as f:
        return json.load(f)


CROPS_DATA: dict[str, CropData] = load_crops_data()


def get_crop_choice():
    return [
        app_commands.Choice(name=crop_id, value=crop_info["description"])
        for crop_id, crop_info in CROPS_DATA.items()
    ]


NUM_OF_CROPS: int = len(CROPS_DATA)
