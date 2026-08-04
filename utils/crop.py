import json


def get_crop_data(crop_name: str) -> dict:

    with open("data/crops.json", "r", encoding="utf-8") as f:
        crops = json.load(f)
    return crops.get(crop_name, {})


def get_all_crops() -> dict:
    with open("data/crops.json", "r", encoding="utf-8") as f:
        crops = json.load(f)
    return crops


def num_of_crops() -> int:
    with open("data/crops.json", "r", encoding="utf-8") as f:
        num = len(json.load(f))
    return num
