"""패시브 트리: 레벨업마다 포인트를 얻어 노드에 투자하면 영구 효과를 얻는다.

효과 이름(passives.json 의 effect / extra_effect):
  sell_price_pct      수확물 판매가 +%
  shop_discount_pct   상점 구매가 -%
  care_points         수확 품질(정성) 점수 +
  mutation_chance     돌연변이 확률 + (0.01 = 1%p)
  golden_chance       황금 작물 확률 + (0.005 = 0.5%p)
  craft_material_pct  제작 재료 -%
  craft_gold_pct      제작 골드 비용 -%
"""

from consistances import PASSIVE_CONFIG, PASSIVE_NODES
from database.codex_repo import CodexRepository
from database.passive_repo import PassiveRepository
from database.user_repo import UserRepository
from service.base import BaseService
from service.codex import codex_counts, codex_effects
from service.user import UserService
from utils.datatype import PassiveBoard, PassiveNodeStatus

EFFECT_LIMITS = {
    # 효과 상한 (데이터 실수로 공짜/음수 가격이 되지 않게)
    "shop_discount_pct": 50,
    "craft_material_pct": 60,
    "craft_gold_pct": 80,
    "grow_time_pct": 50,
}


def apply_limits(effects: dict[str, float]) -> dict[str, float]:
    for effect, limit in EFFECT_LIMITS.items():
        if effect in effects:
            effects[effect] = min(effects[effect], limit)
    return effects


def merge_effects(*sources: dict[str, float]) -> dict[str, float]:
    """여러 출처(패시브, 도감 보상 …)의 효과를 합산하고 상한 적용"""
    merged: dict[str, float] = {}
    for source in sources:
        for effect, value in source.items():
            merged[effect] = merged.get(effect, 0) + value
    return apply_limits(merged)


def compute_effects(ranks: dict[str, int]) -> dict[str, float]:
    effects: dict[str, float] = {}
    for node_id, rank in ranks.items():
        node = PASSIVE_NODES.get(node_id)
        if not node or rank <= 0:
            continue
        rank = min(rank, node["max_rank"])
        effects[node["effect"]] = effects.get(node["effect"], 0) + node["per_rank"] * rank
        if node.get("extra_effect"):
            extra = node["extra_effect"]
            effects[extra] = effects.get(extra, 0) + node["extra_per_rank"] * rank
    return apply_limits(effects)


def total_points(level: int) -> int:
    """레벨 1 은 0포인트, 레벨업마다 points_per_level"""
    return max(0, level - 1) * PASSIVE_CONFIG["points_per_level"]


def reset_cost(level: int) -> int:
    return level * PASSIVE_CONFIG["reset_cost_per_level"]


def is_unlocked(node: dict, ranks: dict[str, int]) -> bool:
    return all(ranks.get(req["node"], 0) >= req["rank"] for req in node["requires"])


def apply_discount(price: int, discount_pct: float) -> int:
    if discount_pct <= 0:
        return price
    return max(1, int(round(price * (1 - discount_pct / 100))))


async def get_effects(conn, user_id: int) -> dict[str, float]:
    """유저에게 적용되는 모든 효과 = 패시브 + 도감 진행도 보상.
    다른 서비스가 같은 트랜잭션 안에서 읽을 때 사용"""
    passive = compute_effects(await PassiveRepository(conn).get_ranks(user_id))
    codex = codex_effects(codex_counts(await CodexRepository(conn).get_entries(user_id)))
    return merge_effects(passive, codex)


class PassiveService(BaseService):

    async def get_board(self, user_id: int) -> PassiveBoard:
        async with self.db.connection() as conn:
            user = await UserRepository(conn).get_user(user_id)
            ranks = await PassiveRepository(conn).get_ranks(user_id)
        return self._build_board(user["farm_exp"], user["gold"], ranks)

    @staticmethod
    def _build_board(exp: int, gold: int, ranks: dict[str, int]) -> PassiveBoard:
        level = UserService.calculate_level(exp)
        spent = sum(ranks.get(n, 0) for n in PASSIVE_NODES)
        total = total_points(level)
        nodes = [
            PassiveNodeStatus(
                node=node,
                rank=ranks.get(node["id"], 0),
                unlocked=is_unlocked(node, ranks),
                maxed=ranks.get(node["id"], 0) >= node["max_rank"],
            )
            for node in PASSIVE_CONFIG["nodes"]
        ]
        return PassiveBoard(
            level=level,
            gold=gold,
            total_points=total,
            spent_points=spent,
            available_points=max(0, total - spent),
            reset_cost=reset_cost(level),
            nodes=nodes,
            effects=compute_effects(ranks),
        )

    async def invest(self, user_id: int, node_id: str) -> str | None:
        """포인트 1 투자. 성공하면 None, 실패하면 사유
        ('unknown' / 'no_points' / 'locked' / 'maxed')"""
        node = PASSIVE_NODES.get(node_id)
        if node is None:
            return "unknown"
        async with self.db.transaction() as conn:
            repo = PassiveRepository(conn)
            user = await repo.lock_user(user_id)  # 동시 투자 방지
            ranks = await repo.get_ranks(user_id)
            board = self._build_board(user["farm_exp"], user["gold"], ranks)
            if board["available_points"] < 1:
                return "no_points"
            if not is_unlocked(node, ranks):
                return "locked"
            if await repo.add_rank(user_id, node_id, node["max_rank"]) is None:
                return "maxed"
            return None

    async def reset(self, user_id: int) -> tuple[bool, int]:
        """골드를 내고 전부 초기화. (성공 여부, 비용). 찍은 게 없으면 무료로 아무것도 안 함"""
        async with self.db.transaction() as conn:
            repo = PassiveRepository(conn)
            user = await repo.lock_user(user_id)
            ranks = await repo.get_ranks(user_id)
            cost = reset_cost(UserService.calculate_level(user["farm_exp"]))
            if not ranks:
                return False, 0
            if await UserRepository(conn).spend_gold(user_id, cost) is None:
                return False, cost
            await repo.reset(user_id)
            return True, cost
