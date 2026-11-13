"""작물 도감: 수확한 작물을 변종별(일반 / 황금 / 돌연변이) 도감에 기록하고,
도감 진행도(발견 수)에 따라 보상 효과(data/codex_rewards.json)를 준다.

같은 작물이라도 일반·황금·돌연변이는 따로 등록된다 (각각 다른 도감).
기록은 FarmService.harvest_crop 이 수확과 같은 트랜잭션에서 CodexRepository.record 로 남긴다.
보상 효과는 service.passive.get_effects 가 패시브 효과와 합쳐서 모든 서비스에 적용한다.
"""

from consistances import CODEX_REWARDS, CROPS_DATA, is_event_crop
from database.codex_repo import CodexRepository
from service.base import BaseService
from utils.datatype import CodexBoard, CodexEntry

NORMAL = "normal"
CODEX_VARIANTS = ("normal", "golden", "mutant")  # 탭 순서


def codex_variant(variant: str | None) -> str:
    """produce 변종(None/'golden'/'mutant') → 도감 구분"""
    return variant or NORMAL


def in_codex(crop: str) -> bool:
    """도감 대상 작물인지. 이벤트 등급 작물과 json 에서 지워진 작물은 제외"""
    return crop in CROPS_DATA and not is_event_crop(crop)


def codex_crops() -> list[str]:
    """도감에 표시할 작물 순서: 등급 → 이름 (이벤트 작물 제외)"""
    return sorted(
        (c for c in CROPS_DATA if in_codex(c)),
        key=lambda c: (CROPS_DATA[c]["buy_grade"], c),
    )


def codex_counts(rows) -> dict[str, int]:
    """변종별 발견한 작물 수 (이벤트 작물 / json 에서 지워진 작물은 제외)"""
    found: dict[str, set[str]] = {v: set() for v in CODEX_VARIANTS}
    for row in rows:
        if row["variant"] in found and in_codex(row["crop_id"]) and row["harvest_count"] > 0:
            found[row["variant"]].add(row["crop_id"])
    return {v: len(crops) for v, crops in found.items()}


def codex_milestones(variant: str, count: int) -> list[dict]:
    """해당 도감의 보상 단계 목록 + 달성 여부"""
    return [
        {**milestone, "achieved": count >= milestone["count"]}
        for milestone in sorted(CODEX_REWARDS.get(variant, []), key=lambda m: m["count"])
    ]


def codex_effects(counts: dict[str, int]) -> dict[str, float]:
    """달성한 모든 도감 보상 효과의 합"""
    effects: dict[str, float] = {}
    for variant in CODEX_VARIANTS:
        for milestone in codex_milestones(variant, counts.get(variant, 0)):
            if milestone["achieved"]:
                effect = milestone["effect"]
                effects[effect] = effects.get(effect, 0) + milestone["value"]
    return effects


def build_codex(rows, variant: str) -> CodexBoard:
    records: dict[str, dict[str, tuple[int, int]]] = {v: {} for v in CODEX_VARIANTS}
    for row in rows:
        if row["variant"] in records and in_codex(row["crop_id"]):
            records[row["variant"]][row["crop_id"]] = (row["harvest_count"], row["best_stars"])

    crops = codex_crops()
    entries = []
    for crop in crops:
        count, best = records[variant].get(crop, (0, 0))
        entries.append(
            CodexEntry(crop=crop, discovered=count > 0, harvest_count=count, best_stars=best)
        )
    counts = codex_counts(rows)
    progress = {v: (counts[v], len(crops)) for v in CODEX_VARIANTS}
    return CodexBoard(
        variant=variant,
        entries=entries,
        discovered=counts[variant],
        total=len(crops),
        progress=progress,
        milestones=codex_milestones(variant, counts[variant]),
        effects=codex_effects(counts),
    )


class CodexService(BaseService):

    async def get_codex(self, user_id: int, variant: str = NORMAL) -> CodexBoard:
        if variant not in CODEX_VARIANTS:
            variant = NORMAL
        async with self.db.connection() as conn:
            rows = await CodexRepository(conn).get_entries(user_id)
        return build_codex(rows, variant)
