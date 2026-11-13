import json
from discord import app_commands
from utils.datatype import CropData, FertilizerData, QuestData


def load_crops_data() -> dict[str, CropData]:
    with open("data/crops.json", "r", encoding="utf-8") as f:
        return json.load(f)


def get_crop_choice():
    return [
        app_commands.Choice(name=crop_id, value=crop_id)
        for crop_id in CROPS_DATA.keys()
    ]


CROPS_DATA: dict[str, CropData] = load_crops_data()


CROP_CHOICES = get_crop_choice()

NUM_OF_CROPS: int = len(CROPS_DATA)


def _load_json(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# 메인 퀘스트: 순서대로 진행되는 고정 퀘스트 체인
MAIN_QUESTS: list[QuestData] = _load_json("data/main_quest.json")
# 일일/주간 퀘스트 풀: 레벨 범위에 맞는 것 중에서 기간마다 뽑는다
QUEST_POOL: dict[str, list[QuestData]] = _load_json("data/quests.json")

# quest_type -> quest_id -> QuestData
QUEST_INDEX: dict[str, dict[str, QuestData]] = {
    "main": {q["id"]: q for q in MAIN_QUESTS},
    **{t: {q["id"]: q for q in pool} for t, pool in QUEST_POOL.items()},
}


# 비료: item_id -> 데이터
FERTILIZERS: dict[str, FertilizerData] = _load_json("data/fertilizers.json")

# 상점 설정 (입고 주기, 슬롯 수, 등급별 재고, 레벨별 등급 확률)
SHOP_CONFIG: dict = _load_json("data/shop.json")
GRADE_NAMES: list[str] = SHOP_CONFIG["grade_names"]

# 수확 품질(별) / 돌연변이 설정
HARVEST_CONFIG: dict = _load_json("data/harvest.json")

# 패시브 트리 (레벨업 포인트로 찍는 영구 효과)
PASSIVE_CONFIG: dict = _load_json("data/passives.json")
PASSIVE_NODES: dict[str, dict] = {n["id"]: n for n in PASSIVE_CONFIG["nodes"]}

# 제작 레시피
RECIPES: list[dict] = _load_json("data/recipes.json")
RECIPE_INDEX: dict[str, dict] = {r["id"]: r for r in RECIPES}


# ------------------------------------------------------------ 등급 / 난이도 도우미
# 이벤트 작물: buy_grade 0. 상점에 나오지 않고(퀘스트 보상 등으로만 획득) 등급 이름은 "이벤트".
# 난이도 0 은 "없음" 으로 품질 감점이 없다.
EVENT_GRADE = 0
EVENT_GRADE_NAME = "이벤트"
NO_DIFFICULTY_NAME = "없음"


def grade_name(grade: int) -> str:
    """1~5 → 최하급~최상급, 0 → 이벤트"""
    if grade == EVENT_GRADE:
        return EVENT_GRADE_NAME
    if 1 <= grade <= len(GRADE_NAMES):
        return GRADE_NAMES[grade - 1]
    return f"{grade}등급"


def is_event_crop(crop: str) -> bool:
    return CROPS_DATA.get(crop, {}).get("buy_grade") == EVENT_GRADE


def difficulty_name(difficulty: int) -> str:
    names = HARVEST_CONFIG["difficulty_names"]
    if 1 <= difficulty <= len(names):
        return names[difficulty - 1]
    return NO_DIFFICULTY_NAME


def difficulty_penalty(difficulty: int) -> int:
    """난이도별 품질 감점. 0(없음)이나 범위 밖이면 감점 없음"""
    penalties = HARVEST_CONFIG["care_points"]["difficulty_penalty"]
    if 1 <= difficulty <= len(penalties):
        return penalties[difficulty - 1]
    return 0


# 도감 진행도 보상: 변종(normal/golden/mutant) -> [{count, effect, value}]
CODEX_REWARDS: dict[str, list[dict]] = _load_json("data/codex_rewards.json")

# 농장 설정: 레벨별 땅 개수 ({min_level, slots} 중 레벨 이하인 마지막 행)
FARM_CONFIG: dict = _load_json("data/farm.json")


# ------------------------------------------------------------ 재로드
def _replace(target, new) -> None:
    """다른 모듈이 `from consistances import X` 로 잡아둔 참조가 그대로 유효하도록
    객체를 새로 만들지 않고 내용만 바꾼다."""
    if isinstance(target, dict):
        target.clear()
        target.update(new)
    else:
        target[:] = new


def reload_static_data() -> None:
    """data/*.json 을 다시 읽어 모든 정적 데이터를 제자리에서 갱신한다.
    파일을 전부 먼저 읽고 검증한 뒤 반영하므로, JSON 오류가 나면 아무것도 바뀌지 않는다."""
    global NUM_OF_CROPS

    crops = load_crops_data()
    main_quests = _load_json("data/main_quest.json")
    quest_pool = _load_json("data/quests.json")
    fertilizers = _load_json("data/fertilizers.json")
    shop_config = _load_json("data/shop.json")
    harvest_config = _load_json("data/harvest.json")
    passive_config = _load_json("data/passives.json")
    recipes = _load_json("data/recipes.json")
    codex_rewards = _load_json("data/codex_rewards.json")
    farm_config = _load_json("data/farm.json")

    # 파생 데이터도 반영 전에 만들어 둔다 (키 누락 등은 여기서 실패)
    quest_index = {
        "main": {q["id"]: q for q in main_quests},
        **{t: {q["id"]: q for q in pool} for t, pool in quest_pool.items()},
    }
    grade_names = shop_config["grade_names"]
    passive_nodes = {n["id"]: n for n in passive_config["nodes"]}
    recipe_index = {r["id"]: r for r in recipes}

    _replace(CROPS_DATA, crops)
    _replace(MAIN_QUESTS, main_quests)
    _replace(QUEST_POOL, quest_pool)
    _replace(QUEST_INDEX, quest_index)
    _replace(FERTILIZERS, fertilizers)
    _replace(SHOP_CONFIG, shop_config)
    # SHOP_CONFIG["grade_names"] 는 새 리스트이므로 GRADE_NAMES 를 다시 연결
    _replace(GRADE_NAMES, grade_names)
    SHOP_CONFIG["grade_names"] = GRADE_NAMES
    _replace(HARVEST_CONFIG, harvest_config)
    _replace(PASSIVE_CONFIG, passive_config)
    _replace(PASSIVE_NODES, passive_nodes)
    _replace(RECIPES, recipes)
    _replace(RECIPE_INDEX, recipe_index)
    _replace(CODEX_REWARDS, codex_rewards)
    _replace(FARM_CONFIG, farm_config)
    _replace(CROP_CHOICES, get_crop_choice())
    NUM_OF_CROPS = len(CROPS_DATA)
