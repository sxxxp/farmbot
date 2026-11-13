from typing import TypedDict


class CropData(TypedDict):
    grow_time: int
    buy_price: int
    sell_price: int
    buy_grade: int
    difficulty: int  # 1 쉬움 / 2 보통 / 3 어려움 (별 받기 난이도)
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
    crop_id: str | None
    planted_at: int | None  # 수확 가능 시각 (Unix)
    is_watered: bool | None
    fertilizer: str | None  # 이번 재배에 사용한 비료 item_id (재배당 1회)


class QuestObjective(TypedDict, total=False):
    type: str  # "plant" | "harvest" | "water" | "level"
    target: str | None  # 작물 이름 (None 이면 아무 작물)
    crop_type: str | None  # 작물 종류 (CropType 이름, 예: "과일"). 생략 가능
    min_stars: int | None  # 수확 별 개수 이상 (harvest 전용). 생략 가능
    count: int  # 목표 횟수 (level 이면 목표 레벨)


class QuestReward(TypedDict):
    gold: int
    exp: int
    items: dict[str, int]  # item_id -> 개수


class QuestData(TypedDict, total=False):
    id: str
    title: str
    description: str
    min_level: int
    max_level: int | None  # 일일/주간 퀘스트만 사용
    objective: QuestObjective
    reward: QuestReward


class QuestStatus(TypedDict):
    quest: QuestData
    progress: int
    completed: bool  # 목표 달성
    claimed: bool  # 보상 수령 완료
    locked: bool  # 레벨 부족 (메인 퀘스트)


class QuestBoard(TypedDict):
    quest_type: str  # "main" | "daily" | "weekly"
    level: int
    quests: list[QuestStatus]
    reset_at: int | None  # 다음 초기화 Unix 타임스탬프 (메인은 None)
    main_cleared: int
    main_total: int


class FertilizerData(TypedDict, total=False):
    name: str
    grade: int  # 1(최하급) ~ 5(최상급) — 상점 등장 등급, 품질 점수(등급만큼)
    reduce_minutes: int  # 남은 성장 시간 단축량 (품질 비료는 0)
    quality_bonus: int  # 추가 품질 점수 (별 확률 ↑). 생략하면 0
    price: int
    description: str


class ShopItem(TypedDict):
    slot: int
    item_id: str  # 씨앗 '<작물>_seed' / 비료 '<등급>_비료'
    category: str  # "seed" | "fertilizer"
    grade: int
    price: int
    stock: int
    bought: int


class ShopBoard(TypedDict):
    level: int
    gold: int
    items: list[ShopItem]
    reset_at: int  # 다음 입고 Unix 타임스탬프
    discount_pct: float  # 패시브 상점 할인율 (items 의 price 는 할인 적용가)


class BuyResult(TypedDict):
    ok: bool
    reason: str | None  # "sold_out" | "no_gold" | "invalid" | None
    item_id: str | None
    amount: int
    cost: int


class HarvestResult(TypedDict):
    crop: str
    stars: int  # 1 ~ 5
    variant: str | None  # "mutant" | "golden" | None
    mutant: bool
    golden: bool
    item_id: str  # 인벤토리에 들어간 수확물 id ('당근|3', '당근|3|M', '당근|3|G')
    level_up: int | None  # 이번 수확으로 레벨이 올랐으면 새 레벨
    new_codex: bool  # 이번 수확으로 도감(작물 x 변종)에 처음 등록됐는지
    rotten: bool  # 너무 오래 방치해 썩은 식물이 나왔는지 (이때 stars=0, item_id=썩은_식물)
    exp: int  # 이번 수확으로 얻은 경험치 (별 배율 + 경험치 보너스 적용, 썩으면 0)


class ProduceItem(TypedDict):
    item_id: str
    crop: str
    stars: int
    variant: str | None  # "mutant" | "golden" | None
    amount: int
    price: int  # 개당 판매가


class WarehouseBoard(TypedDict):
    gold: int
    produce: list[ProduceItem]  # 판매 가능한 수확물 (비싼 순)
    seeds: list[tuple[str, int]]  # (작물 이름, 개수)
    fertilizers: list[tuple[str, int]]  # (item_id, 개수)


class SellResult(TypedDict):
    ok: bool
    sold: int  # 판매한 개수
    earned: int  # 받은 골드


class PassiveNodeStatus(TypedDict):
    node: dict  # passives.json 노드
    rank: int
    unlocked: bool  # 선행 조건 충족
    maxed: bool


class PassiveBoard(TypedDict):
    level: int
    gold: int
    total_points: int
    spent_points: int
    available_points: int
    reset_cost: int
    nodes: list[PassiveNodeStatus]
    effects: dict[str, float]  # 효과 이름 -> 합계


class CraftIngredientStatus(TypedDict):
    spec: dict  # recipes.json 재료 (crop/crop_type/min_stars/variant/count)
    need: int  # 패시브 적용 후 필요 개수
    have: int  # 조건에 맞는 보유 개수


class CraftRecipeStatus(TypedDict):
    recipe: dict
    locked: bool  # 레벨 부족
    gold: int  # 패시브 적용 후 골드 비용
    ingredients: list[CraftIngredientStatus]
    max_crafts: int  # 지금 만들 수 있는 최대 횟수


class CraftBoard(TypedDict):
    level: int
    gold: int
    material_pct: float  # 재료 감소율
    recipes: list[CraftRecipeStatus]


class CodexEntry(TypedDict):
    crop: str
    discovered: bool
    harvest_count: int
    best_stars: int  # 미발견이면 0


class CodexBoard(TypedDict):
    variant: str  # "normal" | "golden" | "mutant"
    entries: list[CodexEntry]  # 모든 작물 (등급 → 이름 순)
    discovered: int
    total: int
    progress: dict[str, tuple[int, int]]  # 변종 -> (발견 수, 전체 수)
    milestones: list[dict]  # 현재 도감의 보상 단계 {count, effect, value, achieved}
    effects: dict[str, float]  # 모든 도감에서 달성한 보상 효과 합계
