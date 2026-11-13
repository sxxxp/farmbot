"""수확물 품질(별 1~5) / 변종(돌연변이·황금) 계산과 수확물 아이템 id 규칙.

수확물 item_id: '<작물>|<별>' (예: '당근|3'), 변종은 뒤에 코드를 붙인다.
  돌연변이 '<작물>|<별>|M', 황금 작물 '<작물>|<별>|G'  (둘은 동시에 붙지 않음)
예전 형식('당근')으로 저장된 수확물은 별 1개 일반 작물로 취급한다.

패시브 효과(effects)는 service.passive.compute_effects 의 결과 dict 를 그대로 받는다.
"""

import random

from consistances import (
    CROPS_DATA,
    EVENT_GRADE,
    FERTILIZERS,
    HARVEST_CONFIG,
    difficulty_penalty,
)

MAX_STARS = 5

# 다 자란 뒤 등급별 제한 시간 안에 수확하지 않으면 작물 대신 이 아이템이 나온다
ROTTEN_ITEM = "썩은_식물"
ROTTEN_NAME = "🥀 썩은 식물"

MUTANT = "mutant"
GOLDEN = "golden"
VARIANT_CODES = {MUTANT: "M", GOLDEN: "G"}
CODE_VARIANTS = {code: variant for variant, code in VARIANT_CODES.items()}
VARIANT_LABELS = {MUTANT: "🧬돌연변이", GOLDEN: "✨황금"}


# ------------------------------------------------------------ item id


def make_produce_id(crop: str, stars: int, variant: str | None = None) -> str:
    if variant:
        return f"{crop}|{stars}|{VARIANT_CODES[variant]}"
    return f"{crop}|{stars}"


def parse_produce_id(item_id: str) -> tuple[str, int, str | None] | None:
    """수확물이면 (작물, 별, 변종 'mutant'/'golden'/None), 씨앗/비료 등이면 None"""
    parts = item_id.split("|")
    crop = parts[0]
    if crop not in CROPS_DATA or len(parts) > 3:
        return None
    if len(parts) == 1:
        return crop, 1, None
    try:
        stars = int(parts[1])
    except ValueError:
        return None
    if not 1 <= stars <= MAX_STARS:
        return None
    if len(parts) == 2:
        return crop, stars, None
    variant = CODE_VARIANTS.get(parts[2])
    if variant is None:
        return None
    return crop, stars, variant


def variant_multiplier(variant: str | None) -> float:
    if variant == MUTANT:
        return HARVEST_CONFIG["mutation"]["price_multiplier"]
    if variant == GOLDEN:
        return HARVEST_CONFIG["golden"]["price_multiplier"]
    return 1.0


def produce_price(item_id: str, sell_bonus_pct: float = 0) -> int:
    """수확물 개당 판매가 = 기본 판매가 x 별 배율 x 변종 배율 x (1 + 판매가 보너스%)
    썩은 식물은 보너스 없이 고정가"""
    if item_id == ROTTEN_ITEM:
        return HARVEST_CONFIG["rot"]["sell_price"]
    parsed = parse_produce_id(item_id)
    if parsed is None:
        return 0
    crop, stars, variant = parsed
    price = (
        CROPS_DATA[crop]["sell_price"]
        * HARVEST_CONFIG["star_price_multipliers"][stars - 1]
        * variant_multiplier(variant)
        * (1 + sell_bonus_pct / 100)
    )
    return int(round(price))


def star_exp(base_exp: int, stars: int) -> int:
    """별 개수에 따른 수확 경험치 (★1 x1.0 ~ ★5 x2.5, data/harvest.json star_exp_multipliers)"""
    multipliers = HARVEST_CONFIG["star_exp_multipliers"]
    stars = min(max(stars, 1), len(multipliers))
    return int(round(base_exp * multipliers[stars - 1]))


def stars_text(stars: int) -> str:
    return "★" * stars + "☆" * (MAX_STARS - stars)


def produce_display_name(item_id: str) -> str:
    """'당근|3' → '당근 ★★★☆☆', '당근|3|M' → '🧬돌연변이 당근 ★★★☆☆', '당근|3|G' → '✨황금 당근 …'"""
    if item_id == ROTTEN_ITEM:
        return ROTTEN_NAME
    parsed = parse_produce_id(item_id)
    if parsed is None:
        return item_id
    crop, stars, variant = parsed
    prefix = f"{VARIANT_LABELS[variant]} " if variant else ""
    return f"{prefix}{crop} {stars_text(stars)}"


# ------------------------------------------------------------ 썩음


def rot_after_seconds(crop: str) -> int:
    """다 자란 뒤 썩기까지 걸리는 시간 = 등급별 시간 x 난이도 배율.
    등급: 최하급 12시간 ~ 최상급 30분 (이벤트 작물은 별도)
    난이도: 쉬움 x1 · 보통 x0.75 · 어려움 x0.5 (0=없음 이나 범위 밖은 x1)"""
    cfg = HARVEST_CONFIG["rot"]
    data = CROPS_DATA[crop]
    grade = data["buy_grade"]
    by_grade = cfg["after_minutes_by_grade"]
    if grade == EVENT_GRADE or not 1 <= grade <= len(by_grade):
        minutes = cfg["event_after_minutes"]
    else:
        minutes = by_grade[grade - 1]

    difficulty = data["difficulty"]
    multipliers = cfg["difficulty_multipliers"]
    if 1 <= difficulty <= len(multipliers):
        minutes *= multipliers[difficulty - 1]
    return max(1, int(round(minutes * 60)))


def rot_at(crop: str, ready_at: int) -> int:
    """이 시각(포함) 이후에 수확하면 썩은 식물이 나온다"""
    return ready_at + rot_after_seconds(crop)


def is_rotten(crop: str, ready_at: int, now: int) -> bool:
    return now >= rot_at(crop, ready_at)


# ------------------------------------------------------------ 품질 계산


def care_points(
    crop: str,
    watered: bool,
    fertilizer: str | None,
    ready_at: int,
    harvested_at: int,
    bonus: int = 0,
) -> int:
    """정성 점수. 높을수록 별이 잘 나온다.
    물주기 / 비료 등급 / 수확 타이밍(다 자라고 바로 → 보너스, 오래 방치 → 감점) /
    작물 난이도 / 패시브 보너스"""
    cfg = HARVEST_CONFIG["care_points"]
    data = CROPS_DATA[crop]
    points = bonus
    if watered:
        points += cfg["watered"]
    if fertilizer in FERTILIZERS:
        data_f = FERTILIZERS[fertilizer]
        points += cfg["fertilizer_per_grade"] * data_f["grade"]
        points += data_f.get("quality_bonus", 0)  # 품질 비료(황금 비료 등)

    delay = max(0, harvested_at - ready_at)
    if delay <= data["grow_time"] * cfg["fresh_ratio"]:
        points += cfg["fresh_bonus"]
    elif delay > data["grow_time"] * cfg["late_ratio"]:
        points -= cfg["late_penalty"]

    points -= difficulty_penalty(data["difficulty"])
    return points


def star_weights(points: int) -> list[int]:
    entry = HARVEST_CONFIG["star_weights"][0]
    for row in HARVEST_CONFIG["star_weights"]:
        if row["min_points"] <= points:
            entry = row
    return entry["weights"]


def star_odds(points: int) -> dict[int, float]:
    """별 개수별 확률(%)"""
    weights = star_weights(points)
    total = sum(weights)
    return {i + 1: w * 100 / total for i, w in enumerate(weights) if w > 0}


def mutation_chance(watered: bool, fertilizer: str | None, bonus: float = 0) -> float:
    cfg = HARVEST_CONFIG["mutation"]
    chance = cfg["base_chance"] + bonus
    if watered:
        chance += cfg["watered_bonus"]
    if fertilizer in FERTILIZERS:
        chance += cfg["fertilizer_bonus_per_grade"] * FERTILIZERS[fertilizer]["grade"]
    return chance


def golden_chance(bonus: float = 0) -> float:
    return HARVEST_CONFIG["golden"]["base_chance"] + bonus


def roll_quality(
    crop: str,
    watered: bool,
    fertilizer: str | None,
    ready_at: int,
    harvested_at: int,
    rng: random.Random | None = None,
    effects: dict | None = None,
) -> tuple[int, str | None]:
    """(별, 변종) 을 뽑는다. 변종은 황금 > 돌연변이 순으로 판정 (동시에 붙지 않음)"""
    rng = rng or random.Random()
    effects = effects or {}
    points = care_points(
        crop, watered, fertilizer, ready_at, harvested_at, effects.get("care_points", 0)
    )
    stars = rng.choices(range(1, MAX_STARS + 1), weights=star_weights(points))[0]

    golden = golden_chance(effects.get("golden_chance", 0))
    mutant = mutation_chance(watered, fertilizer, effects.get("mutation_chance", 0))
    roll = rng.random()
    if roll < golden:
        variant = GOLDEN
    elif roll < golden + mutant:
        variant = MUTANT
    else:
        variant = None
    return stars, variant
