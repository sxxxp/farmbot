import random
from datetime import datetime, timedelta, timezone

from consistances import CROPS_DATA, MAIN_QUESTS, QUEST_INDEX, QUEST_POOL
from database.inventory_repo import InventoryRepository
from database.quest_repo import QuestRepository
from database.user_repo import UserRepository
from service.base import BaseService
from service.user import UserService
from utils.datatype import QuestBoard, QuestData, QuestReward, QuestStatus

QUEST_TYPES = ("main", "daily", "weekly")
# 기간마다 배정되는 퀘스트 개수
PERIOD_QUEST_COUNT = {"daily": 3, "weekly": 2}

# 일일/주간 초기화 기준 시간대 (KST, 매일 0시 / 매주 월요일 0시)
KST = timezone(timedelta(hours=9))


# ------------------------------------------------------------ 기간 계산


def current_period(quest_type: str, now: datetime | None = None) -> str:
    """퀘스트 기간 키. 메인 '' / 일일 '2026-11-12' / 주간 '2026-W46'"""
    if quest_type == "main":
        return ""
    now = (now or datetime.now(KST)).astimezone(KST)
    if quest_type == "daily":
        return now.strftime("%Y-%m-%d")
    iso = now.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def next_reset(quest_type: str, now: datetime | None = None) -> int | None:
    """다음 초기화 시각 (Unix 타임스탬프). 메인은 None"""
    if quest_type == "main":
        return None
    now = (now or datetime.now(KST)).astimezone(KST)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if quest_type == "daily":
        return int((midnight + timedelta(days=1)).timestamp())
    days_until_monday = 7 - now.weekday()  # 월요일 0 → 다음 주 월요일
    return int((midnight + timedelta(days=days_until_monday)).timestamp())


# ------------------------------------------------------------ 퀘스트 선택


def is_available(quest: QuestData, level: int) -> bool:
    max_level = quest.get("max_level")
    return quest["min_level"] <= level and (max_level is None or level <= max_level)


def pick_period_quests(
    user_id: int, quest_type: str, period: str, level: int
) -> list[str]:
    """레벨에 맞는 풀에서 기간 퀘스트를 뽑는다.
    (유저, 기간) 으로 시드를 고정해서 같은 기간에는 항상 같은 결과가 나온다."""
    pool = [q for q in QUEST_POOL.get(quest_type, []) if is_available(q, level)]
    count = min(PERIOD_QUEST_COUNT[quest_type], len(pool))
    rng = random.Random(f"{user_id}:{quest_type}:{period}")
    return [q["id"] for q in rng.sample(pool, count)]


def current_main_quest(claimed_ids: set[str]) -> QuestData | None:
    """보상을 받지 않은 첫 번째 메인 퀘스트 (전부 끝났으면 None)"""
    return next((q for q in MAIN_QUESTS if q["id"] not in claimed_ids), None)


def objective_progress(quest: QuestData, stored: int, level: int) -> int:
    objective = quest["objective"]
    value = level if objective["type"] == "level" else stored
    return min(value, objective["count"])


def objective_matches(
    quest: QuestData, event: str, target: str | None, stars: int | None = None
) -> bool:
    """행동(event)과 작물(target)이 퀘스트 목표에 해당하는지.
    목표의 target(작물 이름)·crop_type(작물 종류)·min_stars(수확 별 개수 이상)는
    지정된 것만 확인한다."""
    objective = quest["objective"]
    if objective["type"] != event:
        return False
    if objective.get("target") is not None and objective["target"] != target:
        return False
    crop_type = objective.get("crop_type")
    if crop_type is not None:
        crop = CROPS_DATA.get(target) if target else None
        if crop is None or crop["crop_type"] != crop_type:
            return False
    min_stars = objective.get("min_stars")
    if min_stars is not None and (stars is None or stars < min_stars):
        return False
    return True


async def _get_level(conn, user_id: int) -> int:
    exp = await UserRepository(conn).get_user_exp(user_id)
    return UserService.calculate_level(exp)


async def _ensure_period_quests(
    repo: QuestRepository, user_id: int, quest_type: str, period: str, level: int
):
    """이번 기간 퀘스트가 없으면 배정하고 진행 목록을 돌려준다"""
    rows = await repo.get_quests(user_id, quest_type, period)
    if rows:
        return rows
    quest_ids = pick_period_quests(user_id, quest_type, period, level)
    await repo.assign_quests(user_id, quest_type, period, quest_ids)
    return await repo.get_quests(user_id, quest_type, period)


async def record_quest_progress(
    conn,
    user_id: int,
    event: str,
    target: str | None = None,
    amount: int = 1,
    stars: int | None = None,
):
    """농사 행동(plant/harvest/water)을 진행 중인 퀘스트에 반영.
    행동과 같은 트랜잭션의 conn 으로 호출한다."""
    repo = QuestRepository(conn)
    level = await _get_level(conn, user_id)

    active: list[tuple[str, QuestData, str]] = []

    main = current_main_quest(await repo.get_claimed_ids(user_id, "main"))
    if main and main["min_level"] <= level:
        active.append(("main", main, ""))

    for quest_type in ("daily", "weekly"):
        period = current_period(quest_type)
        for row in await _ensure_period_quests(repo, user_id, quest_type, period, level):
            quest = QUEST_INDEX[quest_type].get(row["quest_id"])
            if quest and not row["claimed"]:
                active.append((quest_type, quest, period))

    for quest_type, quest, period in active:
        if objective_matches(quest, event, target, stars):
            await repo.add_progress(
                user_id,
                quest_type,
                quest["id"],
                period,
                amount,
                quest["objective"]["count"],
            )


# ---------------------------------------------------------------- 서비스


class QuestService(BaseService):

    async def get_quests(self, user_id: int, quest_type: str) -> QuestBoard:
        async with self.db.transaction() as conn:
            return await self._build_board(conn, user_id, quest_type)

    async def _build_board(self, conn, user_id: int, quest_type: str) -> QuestBoard:
        repo = QuestRepository(conn)
        level = await _get_level(conn, user_id)
        claimed_main = await repo.get_claimed_ids(user_id, "main")
        statuses: list[QuestStatus] = []

        if quest_type == "main":
            quest = current_main_quest(claimed_main)
            if quest:
                rows = await repo.get_quests(user_id, "main", "")
                stored = next(
                    (r["progress"] for r in rows if r["quest_id"] == quest["id"]), 0
                )
                statuses.append(self._status(quest, stored, False, level, True))
        else:
            period = current_period(quest_type)
            rows = await _ensure_period_quests(repo, user_id, quest_type, period, level)
            for row in rows:
                quest = QUEST_INDEX[quest_type].get(row["quest_id"])
                if quest:  # json 에서 삭제된 퀘스트는 무시
                    statuses.append(
                        self._status(quest, row["progress"], row["claimed"], level)
                    )
            order = [q["id"] for q in QUEST_POOL[quest_type]]
            statuses.sort(key=lambda s: order.index(s["quest"]["id"]))

        return QuestBoard(
            quest_type=quest_type,
            level=level,
            quests=statuses,
            reset_at=next_reset(quest_type),
            main_cleared=sum(1 for q in MAIN_QUESTS if q["id"] in claimed_main),
            main_total=len(MAIN_QUESTS),
        )

    @staticmethod
    def _status(
        quest: QuestData,
        stored: int,
        claimed: bool,
        level: int,
        check_level: bool = False,
    ) -> QuestStatus:
        progress = objective_progress(quest, stored, level)
        return QuestStatus(
            quest=quest,
            progress=progress,
            completed=progress >= quest["objective"]["count"],
            claimed=claimed,
            locked=check_level and quest["min_level"] > level,
        )

    async def claim_rewards(self, user_id: int, quest_type: str) -> list[QuestData]:
        """현재 탭에서 완료된 퀘스트 보상을 모두 받는다. 받은 퀘스트 목록 반환"""
        claimed: list[QuestData] = []
        async with self.db.transaction() as conn:
            repo = QuestRepository(conn)
            board = await self._build_board(conn, user_id, quest_type)
            period = current_period(quest_type)

            for status in board["quests"]:
                if status["locked"] or status["claimed"] or not status["completed"]:
                    continue
                quest = status["quest"]
                if await repo.mark_claimed(
                    user_id, quest_type, quest["id"], period, status["progress"]
                ):
                    await self._grant_reward(conn, user_id, quest["reward"])
                    claimed.append(quest)
        return claimed

    @staticmethod
    async def _grant_reward(conn, user_id: int, reward: QuestReward):
        users = UserRepository(conn)
        inventory = InventoryRepository(conn)
        if reward.get("gold"):
            await users.update_user_gold(user_id, reward["gold"])
        if reward.get("exp"):
            await users.update_user_exp(user_id, reward["exp"])
        for item_id, amount in reward.get("items", {}).items():
            await inventory.update_inventory(user_id, item_id, amount)


def sum_rewards(quests: list[QuestData]) -> QuestReward:
    total = QuestReward(gold=0, exp=0, items={})
    for quest in quests:
        reward = quest["reward"]
        total["gold"] += reward.get("gold", 0)
        total["exp"] += reward.get("exp", 0)
        for item_id, amount in reward.get("items", {}).items():
            total["items"][item_id] = total["items"].get(item_id, 0) + amount
    return total
