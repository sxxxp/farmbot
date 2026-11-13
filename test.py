"""
farmbot 테스트

실행 (저장소 루트에서):
    .venv\\Scripts\\python.exe -m unittest test -v

- 기본: DB/디스코드 연결 없이 mock 으로 도는 단위 테스트
- FARMBOT_DB_TEST=1 을 설정하면 config.py 의 PostgreSQL 에 붙는 통합 테스트도 실행
  (하나의 트랜잭션 안에서 실행 후 전부 ROLLBACK 하므로 데이터는 남지 않음.
   테이블은 미리 만들어져 있어야 함: python db_init.py)

  PowerShell:  $env:FARMBOT_DB_TEST=1; .venv\\Scripts\\python.exe -m unittest test -v

@unittest.expectedFailure 가 붙은 테스트는 현재 코드에 있는 알려진 버그를 기록한 것.
버그를 고치면 "unexpected success" 가 뜨니 그때 데코레이터를 지우면 된다.
"""

import os
import sys
import math
import random
import re
import time
import unittest
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

os.chdir(os.path.dirname(os.path.abspath(__file__)))  # data/*.json, ./cogs 상대경로
sys.path.insert(0, os.getcwd())

import discord
from discord.ext import commands

import consistances
from consistances import CROP_CHOICES, CROPS_DATA, NUM_OF_CROPS
from database.farm_repo import FarmRepository
from database.inventory_repo import InventoryRepository
from database.quest_repo import QuestRepository
from database.user_repo import UserRepository
from exceptions import UserNotAdmin, UserNotRegistered
from service.crop import get_crop_data
from service.farm import FarmService
from service.user import UserService
from consistances import MAIN_QUESTS, QUEST_INDEX, QUEST_POOL
from database.connection import SCHEMA_SQL
from service.quest import (
    KST,
    PERIOD_QUEST_COUNT,
    QuestService,
    current_main_quest,
    current_period,
    is_available,
    next_reset,
    objective_matches,
    objective_progress,
    pick_period_quests,
    record_quest_progress,
    sum_rewards,
)
from ui.embed import (
    embed_crop_info,
    embed_profile,
    embed_quests,
    embed_shop,
    format_duration,
    format_reward,
    item_display_name,
    level_progress,
    progress_bar,
    user_header,
    won,
)
from ui.embed import codex_reward_text
from consistances import FERTILIZERS, GRADE_NAMES, SHOP_CONFIG
from database.shop_repo import ShopRepository
from service.shop import (
    ShopService,
    generate_stock,
    grade_odds,
    grade_weights,
    item_category,
    item_grade,
    item_price,
    items_by_grade,
    shop_period,
)
from ui.notice import NOTICE_DELETE_AFTER, send_notice
from consistances import HARVEST_CONFIG
from service.produce import (
    care_points,
    make_produce_id,
    mutation_chance,
    parse_produce_id,
    produce_display_name,
    produce_price,
    roll_quality,
    star_odds,
    star_weights,
    stars_text,
)
from service.warehouse import WarehouseService, build_warehouse
from consistances import PASSIVE_CONFIG, PASSIVE_NODES, RECIPES
from service.craft import CraftService
from service.craft import build_board as build_craft_board
from service.craft import matches as craft_matches
from service.craft import (
    max_crafts,
    plan_consumption,
    plan_many,
    reduced_count,
    reduced_gold,
)
from service.passive import (
    PassiveService,
    apply_discount,
    compute_effects,
    is_unlocked,
    reset_cost,
    total_points,
)
from service.produce import golden_chance
from service.produce import ROTTEN_ITEM, is_rotten, rot_after_seconds, star_exp
from ui.embed import EFFECT_TEXT
from service.codex import CODEX_VARIANTS, build_codex, codex_crops, codex_variant
from service.codex import CodexService
from ui.embed import CODEX_PAGE_SIZE, codex_total_pages, embed_codex
from consistances import CODEX_REWARDS
from service.codex import codex_counts, codex_effects, codex_milestones
from service.farm import bonus_exp, reduced_grow_time
from service.farm import next_slot_level, slots_for_level
from consistances import FARM_CONFIG
from ui.views import level_up_text
from service.passive import get_effects, merge_effects
from consistances import (
    EVENT_GRADE,
    difficulty_name,
    difficulty_penalty,
    grade_name,
    is_event_crop,
)
from ui.embed import embed_warehouse, fertilizer_effect_text
from ui.views import (
    GAME_SCREENS,
    GameHomeView,
    GameNavSelect,
    build_game_screen,
    FarmView,
    FertilizerSelectView,
    QuestView,
    CodexView,
    CraftView,
    PassiveView,
    SeedSelectView,
    ShopView,
    WarehouseView,
)
from utils.dataenum import CropType, SlotStatus
from utils.datatype import CropData
from utils.errorcheck import is_owner, is_registered, is_target_registered

TEST_USER_ID = -424242  # 실제 디스코드 ID 와 겹치지 않는 값 (통합 테스트용)


# ---------------------------------------------------------------- helpers


class _MultiPatch:
    """여러 patch 를 하나처럼 start/stop · with 로 쓰기"""

    def __init__(self, *patches):
        self.patches = patches

    def start(self):
        for p in self.patches:
            p.start()

    def stop(self):
        for p in reversed(self.patches):
            p.stop()

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()


def no_passives(ranks=None, codex_rows=None):
    """service.passive.get_effects 가 빈 패시브(또는 ranks)와 빈 도감(또는 codex_rows)을
    읽도록 하는 patch"""
    repo = MagicMock(get_ranks=AsyncMock(return_value=ranks or {}),
                     lock_user=AsyncMock(return_value={"farm_exp": 0, "gold": 0}))
    codex = MagicMock(get_entries=AsyncMock(return_value=codex_rows or []))
    return _MultiPatch(
        patch("service.passive.PassiveRepository", return_value=repo),
        patch("service.passive.CodexRepository", return_value=codex),
    )


class FakeDB:
    """db.connection() / db.transaction() 이 같은 conn 을 내주는 가짜 Database"""

    def __init__(self, conn=None):
        self.conn = conn if conn is not None else MagicMock(name="conn")

    @asynccontextmanager
    async def connection(self):
        yield self.conn

    @asynccontextmanager
    async def transaction(self):
        yield self.conn


def slot(slot_id, crop_id=None, planted_at=None, is_watered=False):
    return {
        "slot_id": slot_id,
        "crop_id": crop_id,
        "planted_at": planted_at,
        "is_watered": is_watered,
    }


def make_author(user_id=1, name="tester"):
    author = MagicMock(spec=discord.User)
    author.id = user_id
    author.display_name = name
    return author


def make_interaction(user_id=1, values=None):
    interaction = MagicMock()
    interaction.user.id = user_id
    interaction.data = {"values": values or []}

    # 실제처럼 첫 응답 후에는 response.is_done() 이 True
    state = {"done": False}

    def respond(*args, **kwargs):
        state["done"] = True

    interaction.response.is_done = MagicMock(side_effect=lambda: state["done"])
    interaction.response.send_message = AsyncMock(side_effect=respond)
    interaction.response.edit_message = AsyncMock(side_effect=respond)

    # followup.send(wait=True) 가 돌려주는 메시지 (delete(delay=) 확인용)
    interaction.followup_message = MagicMock(delete=AsyncMock())
    interaction.followup.send = AsyncMock(return_value=interaction.followup_message)
    interaction.message = MagicMock()
    interaction.message.edit = AsyncMock()
    return interaction


def assert_notice(test, interaction, text):
    """followup 알림이 나만 보이게 전송되고 10초 뒤 삭제 예약됐는지 확인"""
    call = interaction.followup.send.await_args
    test.assertIn(text, call.args[0])
    test.assertTrue(call.kwargs["ephemeral"])
    test.assertTrue(call.kwargs["wait"])
    interaction.followup_message.delete.assert_awaited_once_with(delay=NOTICE_DELETE_AFTER)


def get_check(decorator):
    """app_commands.check 데코레이터에서 predicate 를 꺼낸다"""

    async def dummy(interaction): ...

    return decorator(dummy).__discord_app_commands_checks__[-1]


def buttons_by_label(view):
    return {c.label: c for c in view.children if isinstance(c, discord.ui.Button)}


def selects(view):
    return [c for c in view.children if isinstance(c, discord.ui.Select)]


# ------------------------------------------------------- data / constants


class CropDataTest(unittest.TestCase):
    def test_crops_json_loaded(self):
        self.assertGreater(len(CROPS_DATA), 0)
        self.assertEqual(NUM_OF_CROPS, len(CROPS_DATA))

    def test_every_crop_matches_schema(self):
        fields = CropData.__annotations__
        for name, data in CROPS_DATA.items():
            with self.subTest(crop=name):
                self.assertEqual(set(data), set(fields))
                for key, typ in fields.items():
                    self.assertIsInstance(data[key], typ)
                self.assertGreater(data["grow_time"], 0)
                self.assertGreaterEqual(data["exp"], 0)

    def test_crop_names_fit_select_option_label(self):
        # SelectOption label 은 최대 100자, 옵션은 최대 25개
        for name in CROPS_DATA:
            self.assertLessEqual(len(name), 100)

    def test_crop_count_and_grades(self):
        self.assertGreaterEqual(len(CROPS_DATA), 106)
        grades = {c["buy_grade"] for c in CROPS_DATA.values()}
        self.assertTrue({1, 2, 3, 4, 5} <= grades)
        self.assertTrue(grades <= {EVENT_GRADE, 1, 2, 3, 4, 5}, grades)

    def test_crop_types_are_known(self):
        # 일반 작물은 CropType 중 하나. 이벤트 작물은 자유롭게 정할 수 있다
        valid = {t.name for t in CropType}
        for name, data in CROPS_DATA.items():
            if not is_event_crop(name):
                self.assertIn(data["crop_type"], valid, name)

    def test_crop_economy(self):
        for name, data in CROPS_DATA.items():
            with self.subTest(crop=name):
                if not is_event_crop(name):  # 이벤트 작물은 상점에서 사지 않음
                    self.assertGreater(data["sell_price"], data["buy_price"])
                self.assertGreater(data["exp"], 0)
                self.assertTrue(data["description"])

    def test_event_grade(self):
        self.assertEqual(grade_name(EVENT_GRADE), "이벤트")
        self.assertEqual(grade_name(1), "최하급")
        self.assertEqual(grade_name(5), "최상급")
        self.assertEqual(grade_name(99), "99등급")  # 잘못된 값도 오류 없이
        self.assertEqual(difficulty_name(0), "없음")
        self.assertEqual(difficulty_name(3), "어려움")
        self.assertEqual(difficulty_penalty(0), 0)
        self.assertEqual(difficulty_penalty(3), HARVEST_CONFIG["care_points"]
                         ["difficulty_penalty"][2])

    def test_event_crops_are_obtainable(self):
        """이벤트 작물은 상점에 없으므로 퀘스트 보상이나 제작으로 씨앗을 얻을 수 있어야 한다"""
        rewards = {i for q in MAIN_QUESTS + QUEST_POOL["daily"] + QUEST_POOL["weekly"]
                   for i in q["reward"]["items"]}
        rewards |= {r["output"]["item"] for r in RECIPES}
        for crop in CROPS_DATA:
            if is_event_crop(crop):
                self.assertIn(f"{crop}_seed", rewards, crop)

    def test_higher_grade_crops_are_bigger_on_average(self):
        def avg(key, grade):
            values = [c[key] for c in CROPS_DATA.values() if c["buy_grade"] == grade]
            return sum(values) / len(values)

        for key in ("grow_time", "buy_price", "exp"):
            averages = [avg(key, g) for g in range(1, 6)]
            self.assertEqual(averages, sorted(averages), key)

    def test_crop_choices(self):
        self.assertEqual([c.value for c in CROP_CHOICES], list(CROPS_DATA))
        self.assertTrue(all(c.name == c.value for c in CROP_CHOICES))

    def test_get_crop_data(self):
        name = next(iter(CROPS_DATA))
        self.assertIs(get_crop_data(name), CROPS_DATA[name])
        self.assertEqual(get_crop_data("존재하지않는작물"), {})

    def test_load_crops_data_returns_fresh_copy(self):
        self.assertEqual(consistances.load_crops_data(), CROPS_DATA)


# ------------------------------------------------------------- exceptions


class ExceptionsTest(unittest.TestCase):
    def test_user_not_registered_default_message(self):
        err = UserNotRegistered(1)
        self.assertIn("/회원가입", err.message)
        self.assertEqual(str(err), err.message)
        self.assertEqual(err.user_id, 1)

    def test_user_not_registered_custom_message(self):
        err = UserNotRegistered(1, "커스텀")
        self.assertEqual(err.message, "커스텀")

    def test_user_not_admin(self):
        self.assertIn("관리자", str(UserNotAdmin()))
        self.assertIsInstance(UserNotAdmin(), discord.app_commands.AppCommandError)


# ----------------------------------------------------------- repositories


class FarmRepositoryTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.conn = MagicMock()
        self.conn.fetch = AsyncMock()
        self.conn.fetchrow = AsyncMock()
        self.conn.execute = AsyncMock()
        self.repo = FarmRepository(self.conn)

    async def test_check_farm_slot_locked_when_no_row(self):
        self.conn.fetchrow.return_value = None
        self.assertEqual(
            await self.repo.check_farm_slot(1, 3), (SlotStatus.LOCKED, None)
        )

    async def test_check_farm_slot_empty(self):
        row = {"crop_id": None, "planted_at": None, "is_watered": False}
        self.conn.fetchrow.return_value = row
        self.assertEqual(await self.repo.check_farm_slot(1, 1), (SlotStatus.EMPTY, row))

    async def test_check_farm_slot_planted(self):
        row = {"crop_id": "당근", "planted_at": 1, "is_watered": False}
        self.conn.fetchrow.return_value = row
        status, _ = await self.repo.check_farm_slot(1, 1)
        self.assertEqual(status, SlotStatus.PLANTED)

    async def test_plant_seed_passes_args(self):
        await self.repo.plant_seed(1, 2, "당근", 999)
        self.assertEqual(self.conn.execute.await_args.args[1:], (1, 2, "당근", 999))

    async def test_harvest_returns_previous_state(self):
        row = {"crop_id": "당근", "planted_at": 900, "is_watered": True,
               "fertilizer": "중급_비료"}
        self.conn.fetchrow.return_value = row
        self.assertEqual(await self.repo.harvest_crop(1, 1), row)
        # 세 번째 인자는 '지금' 타임스탬프 (planted_at <= now 비교용)
        now_arg = self.conn.fetchrow.await_args.args[3]
        self.assertAlmostEqual(now_arg, time.time(), delta=5)
        await self.repo.harvest_crop(1, 1, now=1234)
        self.assertEqual(self.conn.fetchrow.await_args.args[3], 1234)

    async def test_harvest_not_ready_returns_none(self):
        self.conn.fetchrow.return_value = None  # 수확할 게 없음
        self.assertIsNone(await self.repo.harvest_crop(1, 1))


class InventoryRepositoryTest(unittest.IsolatedAsyncioTestCase):
    async def test_update_inventory_passes_signed_delta(self):
        conn = MagicMock(execute=AsyncMock())
        await InventoryRepository(conn).update_inventory(1, "당근_seed", -1)
        self.assertEqual(conn.execute.await_args.args[1:], (1, "당근_seed", -1))


class UserRepositoryTest(unittest.IsolatedAsyncioTestCase):
    async def test_get_user_exp(self):
        conn = MagicMock(fetchrow=AsyncMock(return_value={"farm_exp": 50}))
        self.assertEqual(await UserRepository(conn).get_user_exp(1), 50)

    async def test_get_user_exp_defaults_to_1(self):
        conn = MagicMock(fetchrow=AsyncMock(return_value=None))
        self.assertEqual(await UserRepository(conn).get_user_exp(1), 1)


# --------------------------------------------------------------- services


class UserServiceTest(unittest.IsolatedAsyncioTestCase):
    def test_calculate_level(self):
        cases = {-5: 1, 0: 1, 99: 1, 100: 2, 399: 2, 400: 3, 900: 4, 10000: 11}
        for exp, level in cases.items():
            with self.subTest(exp=exp):
                self.assertEqual(UserService.calculate_level(exp), level)

    async def asyncSetUp(self):
        self.repo = MagicMock()
        self.repo.get_user = AsyncMock()
        self.repo.create_user = AsyncMock()
        patches = [
            patch("service.base.db", FakeDB()),
            patch("service.user.UserRepository", return_value=self.repo),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    async def test_get_user_adds_level(self):
        self.repo.get_user.return_value = {"user_id": 1, "farm_exp": 400, "gold": 100}
        user = await UserService().get_user(1)
        self.assertEqual(user["level"], 3)

    async def test_get_user_missing(self):
        self.repo.get_user.return_value = None
        self.assertIsNone(await UserService().get_user(1))

    async def test_is_user_exist(self):
        self.repo.get_user.return_value = {"user_id": 1}
        self.assertTrue(await UserService().is_user_exist(1))
        self.repo.get_user.return_value = None
        self.assertFalse(await UserService().is_user_exist(1))

    async def test_create_user_returns_none_when_exists(self):
        self.repo.create_user.return_value = None  # ON CONFLICT DO NOTHING
        self.assertIsNone(await UserService().create_user(1))

    async def test_give_item(self):
        inv = MagicMock(update_inventory=AsyncMock())
        with patch("service.user.InventoryRepository", return_value=inv):
            self.assertTrue(await UserService().give_item(1, "당근_seed", 3))
        inv.update_inventory.assert_awaited_once_with(1, "당근_seed", 3)


class FarmServiceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.farm_repo = MagicMock()
        for name in ("check_farm", "check_farm_slot", "plant_seed", "water_seed",
                     "harvest_crop", "ensure_slots"):
            setattr(self.farm_repo, name, AsyncMock())
        self.inv_repo = MagicMock()
        for name in ("get_inventory", "get_item", "update_inventory"):
            setattr(self.inv_repo, name, AsyncMock())
        self.user_repo = MagicMock(update_user_exp=AsyncMock(),
                                   get_user_exp=AsyncMock(return_value=0))
        self.codex_repo = MagicMock(record=AsyncMock(return_value=True))

        patches = [
            patch("service.base.db", FakeDB()),
            no_passives(),
            patch("service.farm.FarmRepository", return_value=self.farm_repo),
            patch("service.farm.InventoryRepository", return_value=self.inv_repo),
            patch("service.farm.UserRepository", return_value=self.user_repo),
            patch("service.farm.CodexRepository", return_value=self.codex_repo),
            patch("service.farm.time", return_value=1000.0),
            patch("service.farm.record_quest_progress", new_callable=AsyncMock),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        import service.farm

        self.quest_hook = service.farm.record_quest_progress
        self.conn = service.base.db.conn

        self.farm_repo.check_farm_slot.return_value = (SlotStatus.EMPTY, {})
        self.inv_repo.get_item.return_value = {"amount": 3}

    # -- plant_seed
    async def test_plant_seed_success(self):
        self.assertTrue(await FarmService().plant_seed(1, 1, "당근"))
        self.inv_repo.update_inventory.assert_awaited_once_with(1, "당근_seed", -1)
        ready_at = 1000 + CROPS_DATA["당근"]["grow_time"]
        self.farm_repo.plant_seed.assert_awaited_once_with(1, 1, "당근", ready_at)
        # 같은 트랜잭션 conn 으로 퀘스트 진행도 반영
        self.quest_hook.assert_awaited_once_with(self.conn, 1, "plant", "당근")

    async def test_plant_seed_fails_without_seed(self):
        for item in (None, {"amount": 0}):
            with self.subTest(item=item):
                self.inv_repo.get_item.return_value = item
                self.assertFalse(await FarmService().plant_seed(1, 1, "당근"))
        self.inv_repo.update_inventory.assert_not_awaited()
        self.farm_repo.plant_seed.assert_not_awaited()
        self.quest_hook.assert_not_awaited()

    async def test_plant_seed_fails_on_locked_or_planted_slot(self):
        for status in (SlotStatus.LOCKED, SlotStatus.PLANTED):
            with self.subTest(status=status):
                self.farm_repo.check_farm_slot.return_value = (status, None)
                self.assertFalse(await FarmService().plant_seed(1, 1, "당근"))
        self.farm_repo.plant_seed.assert_not_awaited()

    async def test_plant_seed_fails_on_unknown_crop(self):
        with patch("builtins.print"):
            self.assertFalse(await FarmService().plant_seed(1, 1, "없는작물"))
        self.farm_repo.plant_seed.assert_not_awaited()

    async def test_plant_seed_returns_false_on_db_error(self):
        self.farm_repo.plant_seed.side_effect = RuntimeError("db down")
        with patch("builtins.print"), patch("traceback.print_exc"):
            self.assertFalse(await FarmService().plant_seed(1, 1, "당근"))

    # -- get_seeds
    async def test_get_seeds_filters_inventory(self):
        self.inv_repo.get_inventory.return_value = [
            {"item_id": "당근_seed", "amount": 2},
            {"item_id": "토마토_seed", "amount": 0},  # 0개
            {"item_id": "당근", "amount": 5},  # 수확물
            {"item_id": "없는작물_seed", "amount": 1},  # crops.json 에 없음
        ]
        self.assertEqual(await FarmService().get_seeds(1), [("당근", 2)])

    async def test_get_seeds_empty(self):
        self.inv_repo.get_inventory.return_value = []
        self.assertEqual(await FarmService().get_seeds(1), [])

    # -- slot helpers
    async def test_slot_helpers(self):
        service = FarmService()
        expected = {
            SlotStatus.LOCKED: (False, False),
            SlotStatus.EMPTY: (True, True),
            SlotStatus.PLANTED: (True, False),
        }
        for status, (valid, empty) in expected.items():
            with self.subTest(status=status):
                self.farm_repo.check_farm_slot.return_value = (status, None)
                self.assertEqual(await service.slot_vaild(1, 1), valid)
                self.assertEqual(await service.slot_empty(1, 1), empty)

    # -- harvest_crop
    async def test_harvest_grants_crop_and_exp(self):
        self.farm_repo.harvest_crop.return_value = {
            "crop_id": "당근", "planted_at": 900, "is_watered": True, "fertilizer": "중급_비료"}
        with patch("service.farm.roll_quality", return_value=(4, "mutant")) as roll:
            result = await FarmService().harvest_crop(1, 1)
        self.assertEqual(result, {"crop": "당근", "stars": 4, "variant": "mutant",
                                  "mutant": True, "golden": False, "item_id": "당근|4|M",
                                  "level_up": None, "new_codex": True, "rotten": False,
                                  "exp": star_exp(CROPS_DATA["당근"]["exp"], 4)})
        self.codex_repo.record.assert_awaited_once_with(1, "당근", "mutant", 4)
        # 수확 직전 상태(물/비료/수확 가능 시각)와 지금 시각, 패시브 효과로 품질 계산
        roll.assert_called_once_with("당근", True, "중급_비료", 900, 1000, effects={})
        self.farm_repo.harvest_crop.assert_awaited_once_with(1, 1, 1000)
        self.inv_repo.update_inventory.assert_awaited_once_with(1, "당근|4|M", 1)
        # ★4 → 별 경험치 배율 적용
        self.user_repo.update_user_exp.assert_awaited_once_with(
            1, star_exp(CROPS_DATA["당근"]["exp"], 4)
        )
        self.quest_hook.assert_awaited_once_with(self.conn, 1, "harvest", "당근", stars=4)

    async def test_harvest_exp_grows_with_stars(self):
        base = CROPS_DATA["토마토"]["exp"]
        gained = []
        for stars in range(1, 6):
            self.farm_repo.harvest_crop.return_value = {
                "crop_id": "토마토", "planted_at": 900, "is_watered": False, "fertilizer": None}
            with patch("service.farm.roll_quality", return_value=(stars, None)):
                gained.append((await FarmService().harvest_crop(1, 1))["exp"])
        self.assertEqual(gained[0], base)  # ★1 은 기본 경험치
        self.assertEqual(gained, sorted(gained))
        self.assertGreater(gained[-1], gained[0])

    def test_star_exp_config(self):
        multipliers = HARVEST_CONFIG["star_exp_multipliers"]
        self.assertEqual(len(multipliers), 5)
        self.assertEqual(multipliers[0], 1.0)
        self.assertEqual(multipliers, sorted(multipliers))
        self.assertEqual(star_exp(10, 5), round(10 * multipliers[4]))
        self.assertEqual(star_exp(10, 0), 10)  # 범위 밖은 ★1 로 취급

    async def test_harvest_rotten_gives_rotten_plant_only(self):
        # 다 자란 시각(900) + 썩는 시간이 지난 뒤 수확 → 썩은 식물, 경험치/퀘스트/도감 없음
        now = 900 + rot_after_seconds("당근")
        self.farm_repo.harvest_crop.return_value = {
            "crop_id": "당근", "planted_at": 900, "is_watered": True, "fertilizer": None}
        with patch("service.farm.time", return_value=now), \
                patch("service.farm.roll_quality") as roll:
            result = await FarmService().harvest_crop(1, 1)
        self.assertTrue(result["rotten"])
        self.assertEqual(result["item_id"], ROTTEN_ITEM)
        self.assertEqual(result["stars"], 0)
        roll.assert_not_called()
        self.inv_repo.update_inventory.assert_awaited_once_with(1, ROTTEN_ITEM, 1)
        self.user_repo.update_user_exp.assert_not_awaited()
        self.quest_hook.assert_not_awaited()
        self.codex_repo.record.assert_not_awaited()

    async def test_harvest_just_before_rot_is_normal(self):
        now = 900 + rot_after_seconds("당근") - 1
        self.farm_repo.harvest_crop.return_value = {
            "crop_id": "당근", "planted_at": 900, "is_watered": False, "fertilizer": None}
        with patch("service.farm.time", return_value=now), \
                patch("service.farm.roll_quality", return_value=(1, None)):
            result = await FarmService().harvest_crop(1, 1)
        self.assertFalse(result["rotten"])
        self.assertEqual(result["item_id"], "당근|1")

    async def test_harvest_event_crop_skips_codex(self):
        event = {**CROPS_DATA["당근"], "buy_grade": EVENT_GRADE}
        self.farm_repo.harvest_crop.return_value = {
            "crop_id": "이벤트작물", "planted_at": 900, "is_watered": False, "fertilizer": None}
        with patch.dict(CROPS_DATA, {"이벤트작물": event}), \
                patch("service.farm.roll_quality", return_value=(3, None)):
            result = await FarmService().harvest_crop(1, 1)
        self.assertEqual(result["item_id"], "이벤트작물|3")  # 수확 자체는 정상
        self.assertFalse(result["new_codex"])
        self.codex_repo.record.assert_not_awaited()
        self.user_repo.update_user_exp.assert_awaited_once()

    async def test_harvest_not_ready(self):
        self.farm_repo.harvest_crop.return_value = None
        self.assertIsNone(await FarmService().harvest_crop(1, 1))
        self.inv_repo.update_inventory.assert_not_awaited()
        self.user_repo.update_user_exp.assert_not_awaited()
        self.quest_hook.assert_not_awaited()

    # -- water / get_farm
    async def test_water_seed(self):
        self.farm_repo.water_seed.return_value = "토마토"
        self.assertTrue(await FarmService().water_seed(1, 2))
        self.farm_repo.water_seed.assert_awaited_once_with(1, 2)
        self.quest_hook.assert_awaited_once_with(self.conn, 1, "water", "토마토")

    async def test_water_seed_noop_does_not_count(self):
        self.farm_repo.water_seed.return_value = None  # 빈 땅 / 이미 물 줌
        self.assertFalse(await FarmService().water_seed(1, 2))
        self.quest_hook.assert_not_awaited()

    async def test_get_farm(self):
        self.farm_repo.check_farm.return_value = [slot(1)]
        self.assertEqual(await FarmService().get_farm(1), [slot(1)])
        self.farm_repo.ensure_slots.assert_awaited_once_with(1, slots_for_level(1))

    async def test_get_farm_opens_slots_for_level(self):
        self.user_repo.get_user_exp.return_value = 1600  # 레벨 5
        await FarmService().get_farm(1)
        self.farm_repo.ensure_slots.assert_awaited_once_with(1, slots_for_level(5))


# farm.json 표를 바꿔도 깨지지 않도록 값은 표에서 읽는다
SLOT_ROWS = FARM_CONFIG["slots_by_level"]


class FarmSlotsTest(unittest.TestCase):
    def test_slot_table(self):
        self.assertEqual(SLOT_ROWS[0], {"min_level": 1, "slots": 1})  # 시작은 1칸 (가입 트리거)
        levels = [r["min_level"] for r in SLOT_ROWS]
        slots = [r["slots"] for r in SLOT_ROWS]
        self.assertEqual(levels, sorted(set(levels)))
        self.assertEqual(slots, sorted(set(slots)))  # 레벨이 오르면 땅은 늘기만 한다

    def test_slots_for_level(self):
        for row, nxt in zip(SLOT_ROWS, SLOT_ROWS[1:] + [None]):
            with self.subTest(row=row):
                self.assertEqual(slots_for_level(row["min_level"]), row["slots"])
                if nxt:
                    # 다음 행 직전 레벨까지는 같은 칸 수
                    self.assertEqual(slots_for_level(nxt["min_level"] - 1), row["slots"])
                    self.assertEqual(next_slot_level(row["min_level"]), nxt["min_level"])
        self.assertEqual(slots_for_level(999), SLOT_ROWS[-1]["slots"])
        self.assertIsNone(next_slot_level(999))

    def test_level_up_text_mentions_new_slots(self):
        second, third = SLOT_ROWS[1], SLOT_ROWS[2]
        text = level_up_text(second["min_level"])
        self.assertIn(f"새 땅 {second['slots'] - 1}칸", text)
        self.assertIn(f"총 {second['slots']}칸", text)
        if third["min_level"] - second["min_level"] > 1:  # 사이 레벨은 땅 증가 없음
            self.assertNotIn("새 땅", level_up_text(second["min_level"] + 1))
        # 여러 레벨이 한 번에 오른 경우: 1 → 세 번째 행 레벨
        self.assertIn(f"새 땅 {third['slots'] - 1}칸", level_up_text(third["min_level"], 1))


# ------------------------------------------------------------ errorcheck


class ErrorCheckTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.service = MagicMock(is_user_exist=AsyncMock(return_value=True))
        p = patch("utils.errorcheck.UserService", return_value=self.service)
        p.start()
        self.addCleanup(p.stop)

    async def test_is_registered(self):
        check = get_check(is_registered())
        self.assertTrue(await check(make_interaction()))
        self.service.is_user_exist.return_value = False
        with self.assertRaises(UserNotRegistered):
            await check(make_interaction())

    async def test_is_target_registered(self):
        check = get_check(is_target_registered("유저"))
        interaction = make_interaction()
        interaction.namespace = SimpleNamespace(유저=SimpleNamespace(id=55))
        self.assertTrue(await check(interaction))
        self.service.is_user_exist.assert_awaited_with(55)

        self.service.is_user_exist.return_value = False
        with self.assertRaisesRegex(UserNotRegistered, "해당 유저"):
            await check(interaction)

    async def test_is_target_registered_without_target_passes(self):
        check = get_check(is_target_registered("유저"))
        interaction = make_interaction()
        interaction.namespace = SimpleNamespace()
        self.assertTrue(await check(interaction))

    async def test_is_owner(self):
        check = get_check(is_owner())
        interaction = make_interaction()
        interaction.client.is_owner = AsyncMock(return_value=True)
        self.assertTrue(await check(interaction))
        interaction.client.is_owner.return_value = False
        with self.assertRaises(UserNotAdmin):
            await check(interaction)


# ------------------------------------------------------------------ embeds


class EmbedTest(unittest.TestCase):
    def test_embed_crop_info(self):
        embed = embed_crop_info("당근")
        data = CROPS_DATA["당근"]
        self.assertEqual(embed.title, "🌱 당근")
        self.assertEqual(embed.description, f"-# {data['description']}")
        fields = {f.name: f.value for f in embed.fields}
        self.assertEqual(fields["⏱️ 성장 시간"], format_duration(data["grow_time"]))
        self.assertEqual(fields["💰 씨앗 가격"], won(data["buy_price"]))
        self.assertEqual(fields["📈 1회 수익"], f"+{won(data['sell_price'] - data['buy_price'])}")
        self.assertEqual(fields["🥀 썩기까지"],
                         f"다 자란 뒤 {format_duration(rot_after_seconds('당근'))}")
        self.assertEqual(fields["작물 종류"], data["crop_type"])
        self.assertEqual(fields["씨앗 등급"], GRADE_NAMES[data["buy_grade"] - 1])
        self.assertTrue(all(f.inline for f in embed.fields))  # 3열 격자

    def test_won_and_level_progress(self):
        self.assertEqual(won(0), "0원")
        self.assertEqual(won(1234567), "1,234,567원")
        self.assertEqual(user_header("t", 3, 1234), "👤 **t** · Lv.**3** · 💰 **1,234원**")
        self.assertEqual(user_header("t"), "👤 **t**")
        # 레벨 2 = 100~400xp. 250xp 면 절반
        self.assertEqual(level_progress(250, 2),
                         "`▰▰▰▰▰▱▱▱▱▱` 250 / 400xp · 다음 레벨까지 **150xp**")
        for exp in (0, 99, 100, 399, 400, 12345):
            level = UserService.calculate_level(exp)
            start, end = UserService.level_exp_range(level)
            self.assertTrue(start <= exp < end, exp)

    def test_format_duration(self):
        cases = {0: "0초", 45: "45초", 150: "2분 30초", 600: "10분",
                 3600: "1시간", 3725: "1시간 2분 5초"}
        for seconds, text in cases.items():
            self.assertEqual(format_duration(seconds), text)

    def test_item_display_name(self):
        self.assertEqual(item_display_name("수박_seed"), "수박 씨앗")
        self.assertEqual(item_display_name("최상급_비료"), "최상급 비료")
        self.assertEqual(item_display_name("알수없는아이템"), "알수없는아이템")
        self.assertEqual(item_display_name("당근"), "당근 ★☆☆☆☆")  # 예전 형식 수확물

    def test_embed_crop_info_unknown_crop(self):
        self.assertIsNone(embed_crop_info("없는작물"))

    def test_embed_profile(self):
        user = {"user_id": 1, "farm_exp": 400, "gold": 100, "level": 3}
        embed = embed_profile(user, "tester")
        self.assertEqual(embed.title, "👤 tester님의 프로필")
        values = [f.value for f in embed.fields]
        self.assertEqual(values, ["Lv.**3**", "**100원**", level_progress(400, 3)])



# -------------------------------------------------------------------- views


class FarmViewTest(unittest.IsolatedAsyncioTestCase):
    # discord.ui.View 는 실행 중인 이벤트 루프가 필요해서 async 테스트로 만든다

    async def asyncSetUp(self):
        self.author = make_author(1)
        self.service = MagicMock()
        for name in ("get_seeds", "plant_seed", "get_farm", "water_seed",
                     "harvest_crop"):
            setattr(self.service, name, AsyncMock())
        p = patch("ui.views.FarmService", return_value=self.service)
        p.start()
        self.addCleanup(p.stop)

    async def test_empty_farm(self):
        view = FarmView([], self.author)
        self.assertEqual(view.total_pages, 1)
        # 땅이 없어도 화면 이동 메뉴는 남는다
        self.assertEqual([type(c) for c in view.children], [GameNavSelect])
        self.assertIn("데이터가 없습니다", view.create_embed(view).description)

    async def test_pagination(self):
        farm = [slot(i) for i in range(1, 13)]
        page1 = FarmView(farm, self.author, page=1)
        page2 = FarmView(farm, self.author, page=2)
        self.assertEqual(page1.total_pages, 2)
        self.assertEqual(len(selects(page1)[0].options), 10)
        self.assertEqual([o.value for o in selects(page2)[0].options], ["11", "12"])

        b1, b2 = buttons_by_label(page1), buttons_by_label(page2)
        self.assertTrue(b1["◀ 이전"].disabled)
        self.assertFalse(b1["다음 ▶"].disabled)
        self.assertFalse(b2["◀ 이전"].disabled)
        self.assertTrue(b2["다음 ▶"].disabled)

    async def test_select_option_labels(self):
        view = FarmView([slot(1, "당근", 100, True), slot(2)], self.author)
        labels = [o.label for o in selects(view)[0].options]
        self.assertEqual(labels, ["1번 땅 - 당근 💧", "2번 땅 - 없음 ☀️"])

    async def test_seed_button_disabled_when_planted(self):
        view = FarmView([slot(1, "당근", 100)], self.author)
        self.assertTrue(buttons_by_label(view)["씨앗 심기"].disabled)
        view = FarmView([slot(1)], self.author)
        self.assertFalse(buttons_by_label(view)["씨앗 심기"].disabled)

    async def test_action_buttons_by_slot_state(self):
        cases = {
            # 상태: (씨앗 심기, 물주기, 수확하기) 비활성화 여부
            "empty": (slot(1), (False, True, True)),
            "planted": (slot(1, "당근", 100, is_watered=False), (True, False, False)),
            "watered": (slot(1, "당근", 100, is_watered=True), (True, True, False)),
        }
        for name, (s, expected) in cases.items():
            with self.subTest(state=name):
                b = buttons_by_label(FarmView([s], self.author))
                actual = (b["씨앗 심기"].disabled, b["물주기"].disabled,
                          b["수확하기"].disabled)
                self.assertEqual(actual, expected)

    async def test_buttons_follow_selected_slot(self):
        farm = [slot(1, "당근", 100), slot(2)]
        self.assertTrue(buttons_by_label(FarmView(farm, self.author, current=1))["씨앗 심기"].disabled)
        self.assertFalse(buttons_by_label(FarmView(farm, self.author, current=2))["씨앗 심기"].disabled)

    async def test_current_defaults_to_first_slot_of_page(self):
        farm = [slot(i) for i in range(1, 13)]
        self.assertEqual(FarmView(farm, self.author).current, 1)
        self.assertEqual(FarmView(farm, self.author, page=2).current, 11)
        # 다른 페이지의 땅을 current 로 주면 페이지 첫 땅으로 보정
        self.assertEqual(FarmView(farm, self.author, page=2, current=3).current, 11)

    async def test_accepts_records_and_unsorted_slots(self):
        class FakeRecord(dict):  # Record 처럼 수정 불가
            def __setitem__(self, *a):
                raise TypeError("Record is immutable")

        view = FarmView([FakeRecord(slot(2)), FakeRecord(slot(1))], self.author)
        self.assertEqual([s["slot_id"] for s in view.farm], [1, 2])
        view.farm[0]["crop_id"] = "당근"  # dict 로 복사됐으므로 가능

    async def test_embed_lists_slots(self):
        now = 1_000_000
        rot = rot_after_seconds("당근")
        farm = [
            slot(1, "당근", now + 60),  # 자라는 중
            slot(2),
            slot(3, "당근", now - 60),  # 수확 가능
            slot(4, "당근", now - rot),  # 썩음
        ]
        with patch("ui.views.time.time", return_value=now):
            embed = FarmView(farm, self.author).create_embed()
        summary, blank, *rows = embed.description.splitlines()
        # 요약: 급한 상태부터, 0개는 생략
        self.assertEqual(
            summary, "✅ 수확 가능 **1** · 🥀 썩음 **1** · 🌱 자라는 중 **1** · ⬜ 빈 땅 **1**"
        )
        self.assertEqual(blank, "")
        self.assertEqual(rows[0], f"▶` 1` 🌱 **당근** · <t:{now + 60}:R> 완료")
        self.assertEqual(rows[1], "　` 2` ⬜ 빈 땅")
        self.assertEqual(
            rows[2], f"　` 3` ✅ **당근** · **수확 가능!** · 🥀 <t:{now - 60 + rot}:R>"
        )
        self.assertEqual(rows[3], "　` 4` 🥀 **당근** · 썩음 · 수확해서 땅을 비우세요")
        self.assertEqual(embed.title, "🌾 농장")
        self.assertTrue(embed.footer.text.startswith("페이지 1/1"))

    async def test_interaction_check(self):
        view = FarmView([slot(1)], self.author)
        self.assertTrue(await view.interaction_check(make_interaction(1)))
        other = make_interaction(2)
        self.assertFalse(await view.interaction_check(other))
        self.assertTrue(other.response.send_message.await_args.kwargs["ephemeral"])

    async def test_select_callback_rebuilds_view_for_selected_slot(self):
        view = FarmView([slot(1, "당근", 100), slot(2)], self.author)
        interaction = make_interaction(values=["2"])
        await view.select_callback(interaction)

        new_view = interaction.response.edit_message.await_args.kwargs["view"]
        self.assertEqual(new_view.current, 2)
        self.assertFalse(buttons_by_label(new_view)["씨앗 심기"].disabled)
        selected = [o.value for o in selects(new_view)[0].options if o.default]
        self.assertEqual(selected, ["2"])

    async def test_page_callbacks(self):
        farm = [slot(i) for i in range(1, 13)]
        view = FarmView(farm, self.author, page=1)
        interaction = make_interaction()
        await view.next_page_callback(interaction)
        self.assertEqual(interaction.response.edit_message.await_args.kwargs["view"].page, 2)

        view = FarmView(farm, self.author, page=2)
        interaction = make_interaction()
        await view.prev_page_callback(interaction)
        self.assertEqual(interaction.response.edit_message.await_args.kwargs["view"].page, 1)

    async def test_seed_button_without_seeds(self):
        self.service.get_seeds.return_value = []
        interaction = make_interaction()
        await FarmView([slot(1)], self.author).seed_button_callback(interaction)
        args = interaction.response.send_message.await_args
        self.assertIn("씨앗이 없습니다", args.args[0])
        self.assertNotIn("view", args.kwargs)
        self.assertTrue(args.kwargs["ephemeral"])
        self.assertEqual(args.kwargs["delete_after"], NOTICE_DELETE_AFTER)

    async def test_seed_button_opens_seed_select(self):
        self.service.get_seeds.return_value = [("당근", 2), ("토마토", 1)]
        interaction = make_interaction()
        farm_view = FarmView([slot(1)], self.author)
        await farm_view.seed_button_callback(interaction)

        kwargs = interaction.response.send_message.await_args.kwargs
        self.assertTrue(kwargs["ephemeral"])
        seed_view = kwargs["view"]
        self.assertIsInstance(seed_view, SeedSelectView)
        self.assertIs(seed_view.farm_message, interaction.message)
        options = selects(seed_view)[0].options
        self.assertEqual([o.value for o in options], ["당근", "토마토"])
        self.assertEqual(options[0].description, "최하급 · 성장 2분 30초 · 보유 2개")

    def edited_view(self, interaction) -> FarmView:
        return interaction.response.edit_message.await_args.kwargs["view"]

    async def test_water_button_refreshes_from_db(self):
        self.service.get_farm.return_value = [slot(1), slot(2, "당근", 100, True)]
        view = FarmView([slot(1), slot(2, "당근", 100)], self.author, current=2)
        interaction = make_interaction()
        await view.water_button_callback(interaction)

        self.service.water_seed.assert_awaited_once_with(1, 2)
        new_view = self.edited_view(interaction)
        self.assertEqual(new_view.current, 2)
        self.assertTrue(new_view.get_slot(2)["is_watered"])
        self.assertTrue(buttons_by_label(new_view)["물주기"].disabled)

    async def test_harvest_button_success(self):
        self.service.harvest_crop.return_value = {
            "crop": "당근", "stars": 3, "mutant": False, "item_id": "당근|3"}
        self.service.get_farm.return_value = [slot(1)]
        view = FarmView([slot(1, "당근", 100)], self.author)
        interaction = make_interaction()
        await view.harvest_button_callback(interaction)

        self.assertIsNone(self.edited_view(interaction).get_slot(1)["crop_id"])
        assert_notice(self, interaction, "당근 ★★★☆☆을(를) 수확했습니다")
        msg = interaction.followup.send.await_args.args[0]
        self.assertIn(f"개당 {produce_price('당근|3')}원", msg)
        self.assertNotIn("돌연변이", msg)
        self.assertNotIn("최고 품질", msg)

    async def test_harvest_button_mutant_max_stars(self):
        self.service.harvest_crop.return_value = {
            "crop": "수박", "stars": 5, "mutant": True, "item_id": "수박|5|M", "exp": 1234}
        self.service.get_farm.return_value = [slot(1)]
        interaction = make_interaction()
        await FarmView([slot(1, "수박", 100)], self.author).harvest_button_callback(interaction)
        msg = interaction.followup.send.await_args.args[0]
        self.assertIn("🧬돌연변이 수박 ★★★★★", msg)
        self.assertIn("돌연변이 발생", msg)
        self.assertIn("최고 품질", msg)
        mult = HARVEST_CONFIG["star_exp_multipliers"][4]
        self.assertIn(f"✨ **경험치 +1,234xp** (★5 보너스 x{mult:g})", msg)

    async def test_harvest_notice_exp_without_star_bonus(self):
        self.service.harvest_crop.return_value = {
            "crop": "당근", "stars": 1, "mutant": False, "item_id": "당근|1", "exp": 10}
        self.service.get_farm.return_value = [slot(1)]
        interaction = make_interaction()
        await FarmView([slot(1, "당근", 100)], self.author).harvest_button_callback(interaction)
        msg = interaction.followup.send.await_args.args[0]
        self.assertIn("✨ **경험치 +10xp**\n", msg)  # ★1 은 보너스 표시 없음
        self.assertNotIn("보너스", msg)

    async def test_quality_hint_in_embed(self):
        future = int(time.time()) + 600
        planted = slot(1, "인삼", future, is_watered=True)
        planted["fertilizer"] = "최상급_비료"
        embed = FarmView([planted, slot(2)], self.author).create_embed()
        [field] = embed.fields
        self.assertEqual(field.name, "📋 상세 정보")
        self.assertTrue(field.value.startswith("**1번 땅 · 인삼**\n"))
        self.assertIn("🌱 자라는 중", field.value)
        self.assertIn("난이도 어려움", field.value)
        self.assertIn("💧 물 줌", field.value)
        self.assertIn("🧪 최상급 비료", field.value)
        self.assertIn("★4 이상", field.value)
        # 빈 땅을 선택하면 심기 안내
        [empty] = FarmView([planted, slot(2)], self.author, current=2).create_embed().fields
        self.assertEqual(empty.name, "📋 상세 정보")
        self.assertTrue(empty.value.startswith("**2번 땅**\n"))
        self.assertIn("씨앗 심기", empty.value)

    async def test_harvest_button_not_ready(self):
        self.service.harvest_crop.return_value = None
        self.service.get_farm.return_value = [slot(1, "당근", 100)]
        view = FarmView([slot(1, "당근", 100)], self.author)
        interaction = make_interaction()
        await view.harvest_button_callback(interaction)

        self.assertEqual(self.edited_view(interaction).get_slot(1)["crop_id"], "당근")
        assert_notice(self, interaction, "아직")


class SeedSelectViewTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.service = MagicMock(plant_seed=AsyncMock(), get_farm=AsyncMock())
        p = patch("ui.views.FarmService", return_value=self.service)
        p.start()
        self.addCleanup(p.stop)

        self.author = make_author(1)
        self.farm_view = FarmView([slot(1), slot(2)], self.author)
        self.farm_message = MagicMock(edit=AsyncMock())

    def make_view(self, seeds):
        return SeedSelectView(self.farm_view, self.farm_message, 2, seeds)

    async def test_single_page_has_no_page_buttons(self):
        view = self.make_view([("인삼", 1), ("당근", 3)])
        self.assertEqual([o.value for o in selects(view)[0].options], ["당근", "인삼"])
        self.assertEqual(buttons_by_label(view), {})

    async def test_paginates_all_seeds(self):
        seeds = [(name, 1) for name in CROPS_DATA]  # 106종
        view = self.make_view(seeds)
        pages = math.ceil(len(seeds) / 25)
        self.assertEqual(view.total_pages, pages)
        self.assertEqual(len(selects(view)[0].options), 25)
        self.assertIn(f"(1/{pages})", selects(view)[0].placeholder)
        # 등급 낮은 순으로 정렬
        grades = [CROPS_DATA[o.value]["buy_grade"] for o in selects(view)[0].options]
        self.assertEqual(grades, sorted(grades))
        b = buttons_by_label(view)
        self.assertTrue(b["◀ 이전"].disabled)
        self.assertFalse(b["다음 ▶"].disabled)

        # 다음 페이지
        interaction = make_interaction()
        await b["다음 ▶"].callback(interaction)
        page2 = interaction.response.edit_message.await_args.kwargs["view"]
        self.assertEqual((page2.page, page2.slot), (2, 2))
        self.assertIs(page2.farm_message, self.farm_message)

        # 모든 씨앗이 정확히 한 번씩 나온다
        shown = []
        for page in range(1, pages + 1):
            v = SeedSelectView(self.farm_view, self.farm_message, 2, seeds, page)
            shown += [o.value for o in selects(v)[0].options]
        self.assertEqual(sorted(shown), sorted(CROPS_DATA))
        last = SeedSelectView(self.farm_view, self.farm_message, 2, seeds, pages)
        self.assertTrue(buttons_by_label(last)["다음 ▶"].disabled)

    async def test_plant_success_refreshes_farm_message(self):
        self.service.plant_seed.return_value = True
        self.service.get_farm.return_value = [slot(1), slot(2, "당근", 500)]
        interaction = make_interaction(values=["당근"])

        await self.make_view([("당근", 1)]).select_callback(interaction)

        self.service.plant_seed.assert_awaited_once_with(1, 2, "당근")
        kwargs = interaction.response.edit_message.await_args.kwargs
        self.assertIn("심었습니다", kwargs["content"])
        self.assertEqual(kwargs["delete_after"], NOTICE_DELETE_AFTER)
        self.assertIsNone(kwargs["view"])

        edit = self.farm_message.edit.await_args.kwargs
        self.assertIsInstance(edit["view"], FarmView)
        self.assertEqual(edit["view"].get_slot(2)["crop_id"], "당근")
        self.assertEqual(edit["view"].current, 2)  # 심은 땅이 계속 선택됨
        self.assertIn("당근", edit["embed"].description)

    async def test_plant_failure_does_not_touch_farm_message(self):
        self.service.plant_seed.return_value = False
        interaction = make_interaction(values=["당근"])

        await self.make_view([("당근", 1)]).select_callback(interaction)

        content = interaction.response.edit_message.await_args.kwargs["content"]
        self.assertIn("심지 못했습니다", content)
        self.assertEqual(
            interaction.response.edit_message.await_args.kwargs["delete_after"],
            NOTICE_DELETE_AFTER,
        )
        self.farm_message.edit.assert_not_awaited()
        self.service.get_farm.assert_not_awaited()


# --------------------------------------------------------------------- cogs


class CogLoadTest(unittest.IsolatedAsyncioTestCase):
    async def test_all_cogs_load_and_register_commands(self):
        bot = commands.Bot(command_prefix="!", intents=discord.Intents.default())
        for filename in sorted(os.listdir("cogs")):
            if filename.endswith(".py") and not filename.startswith("_"):
                with self.subTest(cog=filename):
                    await bot.load_extension(f"cogs.{filename[:-3]}")

        names = {c.name for c in bot.tree.get_commands()}
        for expected in ("농사", "작물", "프로필", "회원가입", "아이템넣기", "도감",
                         "퀘스트", "상점", "창고", "패시브", "제작", "명령어", "게임창"):
            self.assertIn(expected, names)
        await bot.close()

    async def test_crop_autocomplete(self):
        from cogs.farming import Farming

        cog = Farming(bot=None)
        choices = await cog.crop_autocomplete(make_interaction(), "당")
        self.assertEqual([c.value for c in choices], ["당근"])
        all_choices = await cog.crop_autocomplete(make_interaction(), "")
        self.assertEqual(len(all_choices), min(25, len(CROPS_DATA)))

    async def test_crop_info_unknown_crop(self):
        from cogs.farming import Farming

        interaction = make_interaction()
        await Farming.crop_info.callback(Farming(bot=None), interaction, "없는작물")
        args = interaction.response.send_message.await_args
        self.assertIn("존재하지 않습니다", args.args[0])
        self.assertTrue(args.kwargs["ephemeral"])


# ------------------------------------------------------------------ notice


class NoticeTest(unittest.IsolatedAsyncioTestCase):
    def test_delete_after_is_10_seconds(self):
        self.assertEqual(NOTICE_DELETE_AFTER, 10)

    async def test_first_response_uses_send_message_delete_after(self):
        interaction = make_interaction()
        await send_notice(interaction, "보상!")
        interaction.response.send_message.assert_awaited_once_with(
            "보상!", ephemeral=True, delete_after=10
        )
        interaction.followup.send.assert_not_awaited()

    async def test_after_response_uses_followup_and_schedules_delete(self):
        interaction = make_interaction()
        await interaction.response.edit_message(content="화면 갱신")
        await send_notice(interaction, "보상!")
        interaction.followup.send.assert_awaited_once_with(
            "보상!", ephemeral=True, wait=True
        )
        interaction.followup_message.delete.assert_awaited_once_with(delay=10)

    async def test_public_notice(self):
        interaction = make_interaction()
        await send_notice(interaction, "공지", ephemeral=False, delete_after=3)
        interaction.response.send_message.assert_awaited_once_with(
            "공지", ephemeral=False, delete_after=3
        )

    async def test_admin_give_item_notice_is_deleted(self):
        from cogs.admin import Admin

        target = SimpleNamespace(id=55)
        with patch("cogs.admin.UserService") as service:
            service.return_value.give_item = AsyncMock(return_value=True)
            interaction = make_interaction()
            await Admin.giveItem.callback(Admin(bot=None), interaction, target, "당근_seed", 3)
        service.return_value.give_item.assert_awaited_once_with(55, "당근_seed", 3)
        kwargs = interaction.response.send_message.await_args.kwargs
        self.assertEqual(kwargs["delete_after"], NOTICE_DELETE_AFTER)
        self.assertFalse(kwargs["ephemeral"])


# ------------------------------------------------------------------ quests


def quest_row(quest_id, progress=0, claimed=False):
    return {"quest_id": quest_id, "progress": progress, "claimed": claimed}


def make_quest_repo():
    repo = MagicMock()
    for name in ("get_quests", "get_claimed_ids", "assign_quests", "add_progress",
                 "mark_claimed"):
        setattr(repo, name, AsyncMock())
    repo.get_claimed_ids.return_value = set()
    repo.get_quests.return_value = []
    repo.mark_claimed.return_value = True
    return repo


def main_ids_before(quest_id):
    """quest_id 직전까지의 메인 퀘스트 id (= 그 퀘스트가 현재 퀘스트가 되는 상태)"""
    ids = [q["id"] for q in MAIN_QUESTS]
    return set(ids[: ids.index(quest_id)])


class QuestDataTest(unittest.TestCase):
    VALID_TYPES = {"plant", "harvest", "water", "level"}

    def all_quests(self):
        yield "main", MAIN_QUESTS
        for quest_type, pool in QUEST_POOL.items():
            yield quest_type, pool

    def test_pool_types(self):
        self.assertEqual(set(QUEST_POOL), {"daily", "weekly"})

    def test_quest_schema(self):
        for quest_type, quests in self.all_quests():
            ids = [q["id"] for q in quests]
            self.assertEqual(len(ids), len(set(ids)), f"{quest_type} id 중복")
            for quest in quests:
                with self.subTest(quest=quest["id"]):
                    for key in ("id", "title", "description", "min_level",
                                "objective", "reward"):
                        self.assertIn(key, quest)
                    obj = quest["objective"]
                    self.assertIn(obj["type"], self.VALID_TYPES)
                    self.assertGreater(obj["count"], 0)
                    if obj.get("target") is not None:
                        self.assertIn(obj["target"], CROPS_DATA)
                    if obj.get("crop_type") is not None:
                        self.assertIn(obj["crop_type"], {t.name for t in CropType})
                        # 작물 이름과 종류를 동시에 지정하지 않는다
                        self.assertIsNone(obj.get("target"))
                    if obj["type"] == "level":
                        self.assertIsNone(obj.get("crop_type"))
                    reward = quest["reward"]
                    self.assertGreaterEqual(reward["gold"], 0)
                    self.assertGreaterEqual(reward["exp"], 0)
                    for item_id, amount in reward["items"].items():
                        known = (item_id in FERTILIZERS
                                 or (item_id.endswith("_seed")
                                     and item_id.removesuffix("_seed") in CROPS_DATA))
                        self.assertTrue(known, f"알 수 없는 아이템 {item_id}")
                        self.assertGreater(amount, 0)
                    # embed 필드 이름 최대 256자
                    self.assertLess(len(quest["title"]), 200)

    def test_crop_objectives_are_achievable(self):
        """작물/종류 목표가 있는 퀘스트는 열리는 레벨에서 상점에 해당 씨앗이 나올 수 있어야 한다"""
        for quest_type, quests in self.all_quests():
            for quest in quests:
                obj = quest["objective"]
                if obj["type"] == "level" or (obj.get("target") is None
                                              and obj.get("crop_type") is None):
                    continue
                with self.subTest(quest=quest["id"]):
                    matching = [name for name in CROPS_DATA
                                if objective_matches(quest, obj["type"], name, stars=5)]
                    self.assertTrue(matching, "해당하는 작물이 없음")
                    seed_grades = grade_odds("seed", quest["min_level"])
                    rewarded = set()
                    if quest_type == "main":
                        upto = [q["id"] for q in MAIN_QUESTS].index(quest["id"])
                        rewarded = {i for q in MAIN_QUESTS[:upto]
                                    for i in q["reward"]["items"]}
                    obtainable = [n for n in matching
                                  if CROPS_DATA[n]["buy_grade"] in seed_grades
                                  or f"{n}_seed" in rewarded]
                    self.assertTrue(
                        obtainable,
                        f"레벨 {quest['min_level']} 에서 씨앗을 구할 수 없는 목표: {obj}",
                    )

    def test_new_crops_and_crop_types_are_used(self):
        targets = {q["objective"].get("target") for _, qs in self.all_quests() for q in qs}
        crop_types = {q["objective"].get("crop_type") for _, qs in self.all_quests() for q in qs}
        self.assertTrue({"감자", "망고", "사과", "바나나", "샤인머스캣"} <= targets)
        self.assertTrue({"과일", "꽃", "곡물", "채소", "뿌리채소", "허브"} <= crop_types)

    def test_main_quest_levels_are_non_decreasing(self):
        levels = [q["min_level"] for q in MAIN_QUESTS]
        self.assertEqual(levels, sorted(levels))

    def test_level_objectives_are_reachable(self):
        # level 목표는 열리는 레벨보다 낮으면 의미가 없음
        for quest in MAIN_QUESTS:
            if quest["objective"]["type"] == "level":
                self.assertGreaterEqual(quest["objective"]["count"], quest["min_level"])

    def test_first_main_quest_is_free_onboarding(self):
        # 새 유저는 씨앗이 없으므로 첫 퀘스트는 바로 완료 가능해야 한다
        first = MAIN_QUESTS[0]
        self.assertEqual(first["objective"], {"type": "level", "target": None, "count": 1})
        self.assertTrue(any(i.endswith("_seed") for i in first["reward"]["items"]))

    def test_every_level_has_enough_period_quests(self):
        for quest_type, count in PERIOD_QUEST_COUNT.items():
            for level in range(1, 21):
                with self.subTest(type=quest_type, level=level):
                    available = [q for q in QUEST_POOL[quest_type] if is_available(q, level)]
                    self.assertGreaterEqual(len(available), count)

    def test_quest_index(self):
        self.assertEqual(set(QUEST_INDEX), {"main", "daily", "weekly"})
        self.assertIs(QUEST_INDEX["main"]["main_01"], MAIN_QUESTS[0])


class QuestPeriodTest(unittest.TestCase):
    def kst(self, *args):
        return datetime(*args, tzinfo=KST)

    def test_current_period(self):
        now = self.kst(2026, 11, 12, 15, 0)  # 목요일
        self.assertEqual(current_period("main", now), "")
        self.assertEqual(current_period("daily", now), "2026-11-12")
        self.assertEqual(current_period("weekly", now), "2026-W46")

    def test_period_uses_kst(self):
        # UTC 11/12 15:30 = KST 11/13 00:30
        utc = datetime(2026, 11, 12, 15, 30, tzinfo=timezone.utc)
        self.assertEqual(current_period("daily", utc), "2026-11-13")

    def test_weekly_period_changes_on_monday(self):
        sunday = self.kst(2026, 11, 15, 23, 59)
        monday = self.kst(2026, 11, 16, 0, 0)
        self.assertNotEqual(current_period("weekly", sunday), current_period("weekly", monday))

    def test_next_reset(self):
        now = self.kst(2026, 11, 12, 15, 0)
        self.assertIsNone(next_reset("main", now))
        self.assertEqual(next_reset("daily", now), int(self.kst(2026, 11, 13).timestamp()))
        self.assertEqual(next_reset("weekly", now), int(self.kst(2026, 11, 16).timestamp()))
        # 월요일 0시 → 다음 주 월요일
        monday = self.kst(2026, 11, 16)
        self.assertEqual(next_reset("weekly", monday), int(self.kst(2026, 11, 23).timestamp()))


class QuestSelectionTest(unittest.TestCase):
    def test_pick_is_deterministic_and_level_appropriate(self):
        for quest_type, count in PERIOD_QUEST_COUNT.items():
            for level in (1, 2, 3, 10):
                with self.subTest(type=quest_type, level=level):
                    ids = pick_period_quests(42, quest_type, "2026-11-12", level)
                    self.assertEqual(ids, pick_period_quests(42, quest_type, "2026-11-12", level))
                    self.assertEqual(len(ids), count)
                    self.assertEqual(len(set(ids)), count)
                    for quest_id in ids:
                        self.assertTrue(is_available(QUEST_INDEX[quest_type][quest_id], level))

    def test_is_available(self):
        quest = {"min_level": 2, "max_level": 3}
        self.assertEqual([is_available(quest, l) for l in (1, 2, 3, 4)],
                         [False, True, True, False])
        self.assertTrue(is_available({"min_level": 1, "max_level": None}, 99))

    def test_current_main_quest(self):
        self.assertIs(current_main_quest(set()), MAIN_QUESTS[0])
        self.assertIs(current_main_quest({"main_01"}), MAIN_QUESTS[1])
        self.assertIsNone(current_main_quest({q["id"] for q in MAIN_QUESTS}))

    def test_objective_matches(self):
        any_harvest = {"objective": {"type": "harvest", "target": None, "count": 3}}
        carrot = {"objective": {"type": "harvest", "target": "당근", "count": 3}}
        self.assertTrue(objective_matches(any_harvest, "harvest", "오이"))
        self.assertFalse(objective_matches(any_harvest, "plant", "오이"))
        self.assertTrue(objective_matches(carrot, "harvest", "당근"))
        self.assertFalse(objective_matches(carrot, "harvest", "오이"))

    def test_objective_matches_crop_type(self):
        fruit = {"objective": {"type": "harvest", "target": None, "crop_type": "과일",
                               "count": 3}}
        self.assertTrue(objective_matches(fruit, "harvest", "사과"))
        self.assertTrue(objective_matches(fruit, "harvest", "딸기"))
        self.assertFalse(objective_matches(fruit, "harvest", "당근"))  # 뿌리채소
        self.assertFalse(objective_matches(fruit, "plant", "사과"))
        self.assertFalse(objective_matches(fruit, "harvest", None))
        self.assertFalse(objective_matches(fruit, "harvest", "없는작물"))
        water_fruit = {"objective": {"type": "water", "target": None, "crop_type": "과일",
                                     "count": 3}}
        self.assertTrue(objective_matches(water_fruit, "water", "포도"))

    def test_objective_progress(self):
        level_q = {"objective": {"type": "level", "target": None, "count": 3}}
        harvest_q = {"objective": {"type": "harvest", "target": None, "count": 3}}
        self.assertEqual(objective_progress(level_q, 0, 2), 2)
        self.assertEqual(objective_progress(level_q, 0, 5), 3)  # 목표치로 제한
        self.assertEqual(objective_progress(harvest_q, 7, 1), 3)
        self.assertEqual(objective_progress(harvest_q, 1, 9), 1)

    def test_sum_rewards(self):
        total = sum_rewards([
            {"reward": {"gold": 10, "exp": 5, "items": {"당근_seed": 2}}},
            {"reward": {"gold": 20, "exp": 0, "items": {"당근_seed": 1, "오이_seed": 1}}},
        ])
        self.assertEqual(total, {"gold": 30, "exp": 5,
                                 "items": {"당근_seed": 3, "오이_seed": 1}})


class QuestProgressTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.repo = make_quest_repo()
        self.users = MagicMock(get_user_exp=AsyncMock(return_value=0))  # 레벨 1
        patches = [
            patch("service.quest.QuestRepository", return_value=self.repo),
            patch("service.quest.UserRepository", return_value=self.users),
            # 기간 퀘스트 배정을 고정
            patch("service.quest.pick_period_quests",
                  side_effect=lambda uid, t, p, lvl: {
                      "daily": ["daily_plant_3", "daily_carrot_3"],
                      "weekly": ["weekly_plant_15"]}[t]),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

        self.rows = {
            "daily": [quest_row("daily_plant_3"), quest_row("daily_carrot_3")],
            "weekly": [quest_row("weekly_plant_15", claimed=True)],
        }
        self.repo.get_quests.side_effect = lambda uid, t, period: self.rows.get(t, [])

    def progressed(self):
        return {(c.args[1], c.args[2]) for c in self.repo.add_progress.await_args_list}

    async def test_plant_updates_matching_quests(self):
        self.repo.get_claimed_ids.return_value = main_ids_before("main_02")  # 튜토리얼 심기
        crop = QUEST_INDEX["main"]["main_02"]["objective"]["target"]
        await record_quest_progress(MagicMock(), 1, "plant", crop)
        # 주간 퀘스트는 이미 보상을 받아서 제외
        self.assertEqual(self.progressed(),
                         {("main", "main_02"), ("daily", "daily_plant_3")})
        call = next(c for c in self.repo.add_progress.await_args_list
                    if c.args[2] == "main_02")
        self.assertEqual(call.args, (1, "main", "main_02", "", 1, 1))

    async def test_target_must_match(self):
        self.repo.get_claimed_ids.return_value = main_ids_before("main_07")  # 토마토 수확
        self.users.get_user_exp.return_value = 100  # 레벨 2 (main_07 해금)
        tutorial = QUEST_INDEX["main"]["main_07"]["objective"]["target"]
        self.assertIsNotNone(tutorial)

        def expected(crop):
            ids = set()
            if crop == tutorial:
                ids.add(("main", "main_07"))
            if crop == "당근":
                ids.add(("daily", "daily_carrot_3"))
            return ids

        await record_quest_progress(MagicMock(), 1, "harvest", "오이")
        self.assertEqual(self.progressed(), set())
        for crop in {"당근", tutorial}:
            self.repo.add_progress.reset_mock()
            await record_quest_progress(MagicMock(), 1, "harvest", crop)
            self.assertEqual(self.progressed(), expected(crop), crop)

    async def test_crop_type_quests_progress(self):
        self.repo.get_claimed_ids.return_value = main_ids_before("main_14")  # 과일 수확 5
        self.users.get_user_exp.return_value = 900  # 레벨 4
        self.rows["daily"] = [quest_row("daily_harvest_fruit_3"),
                              quest_row("daily_harvest_grain_3")]
        await record_quest_progress(MagicMock(), 1, "harvest", "포도")
        self.assertEqual(self.progressed(),
                         {("main", "main_14"), ("daily", "daily_harvest_fruit_3")})

        self.repo.add_progress.reset_mock()
        await record_quest_progress(MagicMock(), 1, "harvest", "보리")
        self.assertEqual(self.progressed(), {("daily", "daily_harvest_grain_3")})

    async def test_locked_main_quest_does_not_progress(self):
        # main_07 (토마토 수확) 은 레벨 2 부터
        self.repo.get_claimed_ids.return_value = main_ids_before("main_07")
        await record_quest_progress(MagicMock(), 1, "harvest", "토마토")
        self.assertNotIn(("main", "main_07"), self.progressed())

    async def test_assigns_period_quests_when_missing(self):
        self.rows["daily"] = []
        assigned = {}

        async def assign(uid, t, period, ids):
            assigned[t] = ids
            self.rows[t] = [quest_row(i) for i in ids]

        self.repo.assign_quests.side_effect = assign
        await record_quest_progress(MagicMock(), 1, "plant", "당근")
        self.assertEqual(assigned, {"daily": ["daily_plant_3", "daily_carrot_3"]})
        self.assertIn(("daily", "daily_plant_3"), self.progressed())


class QuestServiceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.repo = make_quest_repo()
        self.users = MagicMock(get_user_exp=AsyncMock(return_value=0),
                               update_user_gold=AsyncMock(), update_user_exp=AsyncMock())
        self.inventory = MagicMock(update_inventory=AsyncMock())
        patches = [
            patch("service.base.db", FakeDB()),
            patch("service.quest.QuestRepository", return_value=self.repo),
            patch("service.quest.UserRepository", return_value=self.users),
            patch("service.quest.InventoryRepository", return_value=self.inventory),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    async def test_main_board_shows_current_quest(self):
        board = await QuestService().get_quests(1, "main")
        self.assertEqual(board["level"], 1)
        self.assertEqual(board["main_cleared"], 0)
        self.assertEqual(board["main_total"], len(MAIN_QUESTS))
        self.assertIsNone(board["reset_at"])
        [status] = board["quests"]
        self.assertEqual(status["quest"]["id"], "main_01")
        self.assertTrue(status["completed"])  # 레벨 1 달성 퀘스트
        self.assertFalse(status["locked"])

    async def test_main_board_uses_stored_progress(self):
        self.repo.get_claimed_ids.return_value = main_ids_before("main_05")
        self.repo.get_quests.return_value = [quest_row("main_05", 2)]
        [status] = (await QuestService().get_quests(1, "main"))["quests"]
        self.assertEqual((status["progress"], status["completed"]), (2, False))

    async def test_main_board_locked_by_level(self):
        self.repo.get_claimed_ids.return_value = main_ids_before("main_07")
        [status] = (await QuestService().get_quests(1, "main"))["quests"]
        self.assertTrue(status["locked"])

    async def test_main_board_all_done(self):
        self.repo.get_claimed_ids.return_value = {q["id"] for q in MAIN_QUESTS}
        board = await QuestService().get_quests(1, "main")
        self.assertEqual(board["quests"], [])
        self.assertEqual(board["main_cleared"], len(MAIN_QUESTS))

    async def test_daily_board_level_changes_quests(self):
        for exp, level in ((0, 1), (400, 3)):
            with self.subTest(level=level):
                self.users.get_user_exp.return_value = exp
                rows = []
                self.repo.get_quests.side_effect = lambda *a: list(rows)

                async def assign(uid, t, period, ids):
                    rows[:] = [quest_row(i) for i in ids]

                self.repo.assign_quests.side_effect = assign
                board = await QuestService().get_quests(1, "daily")
                self.assertEqual(board["level"], level)
                self.assertEqual(len(board["quests"]), PERIOD_QUEST_COUNT["daily"])
                for status in board["quests"]:
                    self.assertTrue(is_available(status["quest"], level))
                self.assertIsNotNone(board["reset_at"])

    async def test_daily_board_ignores_removed_quests(self):
        self.repo.get_quests.return_value = [quest_row("daily_plant_3", 3),
                                             quest_row("삭제된_퀘스트")]
        board = await QuestService().get_quests(1, "daily")
        [status] = board["quests"]
        self.assertTrue(status["completed"])

    async def test_claim_main_quest_grants_reward(self):
        claimed = await QuestService().claim_rewards(1, "main")
        self.assertEqual([q["id"] for q in claimed], ["main_01"])
        self.repo.mark_claimed.assert_awaited_once_with(1, "main", "main_01", "", 1)
        reward = MAIN_QUESTS[0]["reward"]
        self.users.update_user_gold.assert_awaited_once_with(1, reward["gold"])
        for item_id, amount in reward["items"].items():
            self.inventory.update_inventory.assert_any_await(1, item_id, amount)

    async def test_claim_skips_incomplete_locked_and_claimed(self):
        self.repo.get_claimed_ids.return_value = main_ids_before("main_07")  # 잠김
        self.assertEqual(await QuestService().claim_rewards(1, "main"), [])

        self.repo.get_claimed_ids.return_value = main_ids_before("main_05")
        self.repo.get_quests.return_value = [quest_row("main_05", 1)]  # 미완료
        self.assertEqual(await QuestService().claim_rewards(1, "main"), [])
        self.repo.mark_claimed.assert_not_awaited()

    async def test_claim_daily_only_completed(self):
        self.repo.get_quests.return_value = [
            quest_row("daily_plant_3", 3),
            quest_row("daily_water_3", 1),
            quest_row("daily_harvest_3", 3, claimed=True),
        ]
        claimed = await QuestService().claim_rewards(1, "daily")
        self.assertEqual([q["id"] for q in claimed], ["daily_plant_3"])

    async def test_claim_race_does_not_double_grant(self):
        self.repo.mark_claimed.return_value = False  # 다른 요청이 먼저 받음
        self.assertEqual(await QuestService().claim_rewards(1, "main"), [])
        self.users.update_user_gold.assert_not_awaited()
        self.inventory.update_inventory.assert_not_awaited()


def make_board(quest_type="main", quests=None, level=1, reset_at=None):
    return {"quest_type": quest_type, "level": level, "quests": quests or [],
            "reset_at": reset_at, "main_cleared": 0, "main_total": len(MAIN_QUESTS)}


def make_status(quest, progress=0, completed=False, claimed=False, locked=False):
    return {"quest": quest, "progress": progress, "completed": completed,
            "claimed": claimed, "locked": locked}


class QuestEmbedTest(unittest.TestCase):
    def test_helpers(self):
        self.assertEqual(item_display_name("당근_seed"), "당근 씨앗")
        self.assertEqual(item_display_name("알수없는아이템"), "알수없는아이템")
        self.assertEqual(item_display_name("당근"), "당근 ★☆☆☆☆")  # 예전 형식 수확물
        self.assertEqual(progress_bar(0, 4), "▱" * 10)
        self.assertEqual(progress_bar(2, 4), "▰" * 5 + "▱" * 5)
        self.assertEqual(progress_bar(4, 4), "▰" * 10)
        self.assertEqual(format_reward({"gold": 10, "exp": 0, "items": {"당근_seed": 2}}),
                         "💰 10원 · 🎒 당근 씨앗 x2")
        self.assertEqual(format_reward({"gold": 0, "exp": 0, "items": {}}), "없음")

    def test_main_embed(self):
        quest = QUEST_INDEX["main"]["main_07"]  # 토마토 3회 수확
        embed = embed_quests(make_board(quests=[make_status(quest, 0)]), "tester")
        self.assertEqual(embed.title, "📜 메인 퀘스트")
        self.assertIn("Lv.**1**", embed.description)
        self.assertNotIn("초기화", embed.description)
        [field] = embed.fields
        self.assertEqual(field.name, f"▫️ {quest['title']}")
        objective = quest["objective"]
        self.assertIn(f"{objective['target']} 0/{objective['count']}회 수확", field.value)
        self.assertIn("보상:", field.value)

    def test_main_embed_any_crop_has_no_target_name(self):
        quest = QUEST_INDEX["main"]["main_04"]  # 아무 작물 1회 수확 (target 없음)
        self.assertIsNone(quest["objective"]["target"])
        [field] = embed_quests(make_board(quests=[make_status(quest, 0)]), "t").fields
        self.assertIn("` 0/1회 수확", field.value)
        self.assertNotIn("None", field.value)

    def test_status_icons_and_locked(self):
        quest = QUEST_INDEX["main"]["main_07"]
        cases = [
            (make_status(quest, locked=True), "🔒"),
            (make_status(quest, 3, completed=True), "🎁"),
            (make_status(quest, 3, completed=True, claimed=True), "✅"),
        ]
        for status, icon in cases:
            [field] = embed_quests(make_board(quests=[status]), "t").fields
            self.assertTrue(field.name.startswith(icon))
        [field] = embed_quests(make_board(quests=[cases[0][0]]), "t").fields
        self.assertIn("레벨 2 이상 필요", field.value)

    def test_level_objective_text(self):
        quest = QUEST_INDEX["main"]["main_06"]
        [field] = embed_quests(make_board(quests=[make_status(quest, 1)]), "t").fields
        self.assertIn("레벨 1/2", field.value)

    def test_crop_type_objective_text(self):
        quest = QUEST_INDEX["daily"]["daily_harvest_fruit_3"]
        [field] = embed_quests(make_board("daily", quests=[make_status(quest, 2)]), "t").fields
        self.assertIn("과일 2/3회 수확", field.value)
        quest = QUEST_INDEX["main"]["main_12"]  # 비료 보상 표시
        [field] = embed_quests(make_board(quests=[make_status(quest)]), "t").fields
        self.assertIn("중급 비료 x2", field.value)

    def test_daily_embed_and_empty_states(self):
        embed = embed_quests(make_board("daily", reset_at=123), "t")
        self.assertIn("<t:123:R>", embed.description)
        self.assertIn("퀘스트가 없습니다", embed.fields[0].value)
        embed = embed_quests(make_board("main"), "t")
        self.assertIn("모든 메인 퀘스트를 완료", embed.fields[0].value)


class QuestViewTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.author = make_author(1)
        self.service = MagicMock(get_quests=AsyncMock(), claim_rewards=AsyncMock())
        p = patch("ui.views.QuestService", return_value=self.service)
        p.start()
        self.addCleanup(p.stop)
        self.quest = QUEST_INDEX["main"]["main_01"]

    async def test_tab_buttons(self):
        view = QuestView(self.author, make_board("daily"))
        buttons = buttons_by_label(view)
        self.assertTrue(buttons["일일"].disabled)
        self.assertEqual(buttons["일일"].style, discord.ButtonStyle.primary)
        self.assertFalse(buttons["메인"].disabled)
        self.assertFalse(buttons["주간"].disabled)

    async def test_claim_button_counts_claimable(self):
        statuses = [
            make_status(self.quest, 1, completed=True),
            make_status(self.quest, 1, completed=True, claimed=True),
            make_status(self.quest, 0),
            make_status(self.quest, 1, completed=True, locked=True),
        ]
        view = QuestView(self.author, make_board(quests=statuses))
        self.assertEqual(view.claimable, 1)
        self.assertFalse(buttons_by_label(view)["🎁 보상 받기 (1)"].disabled)
        empty = QuestView(self.author, make_board())
        self.assertTrue(buttons_by_label(empty)["🎁 보상 받기 (0)"].disabled)

    async def test_tab_click_redraws_embed(self):
        self.service.get_quests.return_value = make_board("weekly", reset_at=1)
        view = QuestView(self.author, make_board("main"))
        interaction = make_interaction()
        await buttons_by_label(view)["주간"].callback(interaction)

        self.service.get_quests.assert_awaited_once_with(1, "weekly")
        kwargs = interaction.response.edit_message.await_args.kwargs
        self.assertEqual(kwargs["embed"].title, "🗓️ 주간 퀘스트")
        self.assertEqual(kwargs["view"].quest_type, "weekly")

    async def test_claim_callback(self):
        self.service.claim_rewards.return_value = [self.quest]
        self.service.get_quests.return_value = make_board()
        board = make_board(quests=[make_status(self.quest, 1, completed=True)])
        interaction = make_interaction()
        await QuestView(self.author, board).claim_callback(interaction)

        self.service.claim_rewards.assert_awaited_once_with(1, "main")
        interaction.response.edit_message.assert_awaited_once()
        assert_notice(self, interaction, self.quest["title"])
        msg = interaction.followup.send.await_args.args[0]
        self.assertIn(self.quest["title"], msg)
        self.assertIn("100원", msg)

    async def test_claim_nothing(self):
        self.service.claim_rewards.return_value = []
        self.service.get_quests.return_value = make_board()
        interaction = make_interaction()
        await QuestView(self.author, make_board()).claim_callback(interaction)
        assert_notice(self, interaction, "없습니다")

    async def test_interaction_check(self):
        view = QuestView(self.author, make_board())
        self.assertFalse(await view.interaction_check(make_interaction(2)))
        self.assertTrue(await view.interaction_check(make_interaction(1)))

    async def test_quest_command(self):
        from cogs.quests import Quests

        board = make_board()
        with patch("cogs.quests.QuestService") as service:
            service.return_value.get_quests = AsyncMock(return_value=board)
            interaction = make_interaction(1)
            interaction.user = self.author
            await Quests.quests.callback(Quests(bot=None), interaction)
        service.return_value.get_quests.assert_awaited_once_with(1, "main")
        kwargs = interaction.response.send_message.await_args.kwargs
        self.assertIsInstance(kwargs["view"], QuestView)
        self.assertEqual(kwargs["embed"].title, "📜 메인 퀘스트")


# --------------------------------------------------------- shop / fertilizer


def expected_grade(odds: dict[int, float]) -> float:
    return sum(g * p for g, p in odds.items()) / 100


class ShopDataTest(unittest.TestCase):
    def test_fertilizers(self):
        # 성장 촉진 비료: 등급마다 1종, 등급이 높을수록 많이 단축되고 비쌈
        speed = sorted((f for f in FERTILIZERS.values() if f.get("reduce_minutes")),
                       key=lambda f: f["grade"])
        self.assertEqual([f["grade"] for f in speed], [1, 2, 3, 4, 5])
        for prev, cur in zip(speed, speed[1:]):
            self.assertGreater(cur["reduce_minutes"], prev["reduce_minutes"])
            self.assertGreater(cur["price"], prev["price"])
        for f in speed:
            self.assertTrue(f["name"].startswith(GRADE_NAMES[f["grade"] - 1]))
        for item_id, data in FERTILIZERS.items():
            self.assertTrue(item_id.endswith("_비료"))
            self.assertEqual(data["name"], item_display_name(item_id))
            self.assertIn(data["grade"], range(1, 6))
            # 효과가 하나도 없는 비료는 없어야 한다
            self.assertTrue(data.get("reduce_minutes") or data.get("quality_bonus"))

    def test_quality_fertilizers_have_5_tiers(self):
        quality = sorted((f for f in FERTILIZERS.values() if f.get("quality_bonus")),
                         key=lambda f: f["grade"])
        self.assertEqual([f["grade"] for f in quality], [1, 2, 3, 4, 5])
        for prev, cur in zip(quality, quality[1:]):
            self.assertGreater(cur["quality_bonus"], prev["quality_bonus"])
            self.assertGreater(cur["price"], prev["price"])
        for f in quality:
            self.assertEqual(f["reduce_minutes"], 0)
            self.assertEqual(f["name"], f"{GRADE_NAMES[f['grade'] - 1]} 황금 비료")

    def test_quality_fertilizer_beats_speed_fertilizer_of_same_grade(self):
        for name in GRADE_NAMES:
            speed = care_points("당근", False, f"{name}_비료", 0, 0)
            quality = care_points("당근", False, f"{name}_황금_비료", 0, 0)
            self.assertGreater(quality, speed)

    def test_fertilizer_effect_text(self):
        self.assertIn("별 확률", fertilizer_effect_text("하급_황금_비료"))
        self.assertNotIn("단축", fertilizer_effect_text("하급_황금_비료"))
        self.assertIn("5분 단축", fertilizer_effect_text("중급_비료"))

    def test_every_grade_has_seeds_and_fertilizers(self):
        for category in ("seed", "fertilizer"):
            self.assertEqual(sorted(items_by_grade(category)), [1, 2, 3, 4, 5])

    def test_config(self):
        self.assertEqual(24 % SHOP_CONFIG["refresh_hours"], 0)
        self.assertEqual(SHOP_CONFIG["refresh_hours"], 3)
        self.assertEqual(len(GRADE_NAMES), 5)
        for category in ("seed", "fertilizer"):
            with self.subTest(category=category):
                table = SHOP_CONFIG["grade_weights"][category]
                levels = [row["min_level"] for row in table]
                self.assertEqual(levels[0], 1)
                self.assertEqual(levels, sorted(set(levels)))
                for row in table:
                    self.assertEqual(len(row["weights"]), 5)
                    self.assertTrue(all(w >= 0 for w in row["weights"]))
                    self.assertGreater(sum(row["weights"]), 0)
                stock = SHOP_CONFIG["stock_by_grade"][category]
                self.assertEqual(len(stock), 5)
                self.assertTrue(all(s > 0 for s in stock))
                total_items = sum(len(v) for v in items_by_grade(category).values())
                self.assertLessEqual(SHOP_CONFIG["slots"][category], total_items)

    def test_select_menu_fits(self):
        self.assertLessEqual(sum(SHOP_CONFIG["slots"].values()), 25)


class ShopOddsTest(unittest.TestCase):
    def test_grade_weights_lookup(self):
        table = SHOP_CONFIG["grade_weights"]["fertilizer"]
        self.assertEqual(grade_weights("fertilizer", 1), table[0]["weights"])
        self.assertEqual(grade_weights("fertilizer", 4), table[1]["weights"])  # 3~4
        self.assertEqual(grade_weights("fertilizer", 99), table[-1]["weights"])

    def test_odds_sum_to_100(self):
        for category in ("seed", "fertilizer"):
            for level in range(1, 21):
                self.assertAlmostEqual(sum(grade_odds(category, level).values()), 100)

    def test_low_level_has_no_top_grade_fertilizer(self):
        for level in (1, 2, 3, 4):
            self.assertNotIn(5, grade_odds("fertilizer", level))

    def test_high_level_has_no_bottom_grade_fertilizer(self):
        for level in range(5, 21):
            self.assertNotIn(1, grade_odds("fertilizer", level))
        self.assertIn(1, grade_odds("fertilizer", 1))

    def test_better_grades_more_likely_with_level(self):
        for category in ("seed", "fertilizer"):
            with self.subTest(category=category):
                expected = [expected_grade(grade_odds(category, l)) for l in range(1, 21)]
                self.assertEqual(expected, sorted(expected))
                self.assertGreater(expected[-1], expected[0] + 1)
                high = [sum(p for g, p in grade_odds(category, l).items() if g >= 4)
                        for l in range(1, 21)]
                self.assertEqual(high, sorted(high))


class ShopPeriodTest(unittest.TestCase):
    def kst(self, *args):
        return datetime(*args, tzinfo=KST)

    def test_three_hour_blocks(self):
        cases = [
            (self.kst(2026, 11, 12, 0, 0), "2026-11-12T00", self.kst(2026, 11, 12, 3)),
            (self.kst(2026, 11, 12, 10, 30), "2026-11-12T09", self.kst(2026, 11, 12, 12)),
            (self.kst(2026, 11, 12, 23, 59), "2026-11-12T21", self.kst(2026, 11, 13, 0)),
        ]
        for now, key, reset in cases:
            with self.subTest(now=now):
                self.assertEqual(shop_period(now), (key, int(reset.timestamp())))

    def test_uses_kst(self):
        utc = datetime(2026, 11, 12, 15, 0, tzinfo=timezone.utc)  # KST 11/13 00:00
        self.assertEqual(shop_period(utc)[0], "2026-11-13T00")


class EventCropTest(unittest.IsolatedAsyncioTestCase):
    """이벤트 등급(buy_grade 0) 작물: 상점 미판매 · 등급 이름 '이벤트' · 난이도 감점 없음"""

    EVENT = {"grow_time": 60, "buy_price": 1, "sell_price": 10, "buy_grade": 0,
             "difficulty": 0, "exp": 5, "crop_type": "이벤트", "description": "테스트"}

    def setUp(self):
        p = patch.dict(CROPS_DATA, {"이벤트작물": self.EVENT})
        p.start()
        self.addCleanup(p.stop)

    def test_not_sold_in_shop(self):
        self.assertTrue(is_event_crop("이벤트작물"))
        self.assertFalse(is_event_crop("당근"))
        for grade, ids in items_by_grade("seed").items():
            self.assertNotIn("이벤트작물_seed", ids)
            self.assertNotEqual(grade, EVENT_GRADE)
        for n in range(50):
            ids = [s["item_id"] for s in generate_stock(n, "p", 12)]
            self.assertNotIn("이벤트작물_seed", ids)

    def test_crop_info_embed(self):
        fields = {f.name: f.value for f in embed_crop_info("이벤트작물").fields}
        self.assertEqual(fields["씨앗 등급"], "이벤트 (상점 미판매)")
        self.assertEqual(fields["재배 난이도"], "없음")

    async def test_seed_select_and_quality_hint(self):
        farm_view = FarmView([slot(1)], make_author(1))
        view = SeedSelectView(farm_view, MagicMock(), 1, [("당근", 1), ("이벤트작물", 2)])
        options = selects(view)[0].options
        self.assertEqual(options[0].value, "이벤트작물")  # 이벤트 작물이 맨 앞
        self.assertTrue(options[0].description.startswith("이벤트 ·"))
        planted = slot(1, "이벤트작물", int(time.time()) + 60)
        embed = FarmView([planted], make_author(1)).create_embed()
        self.assertIn("난이도 없음", embed.fields[0].value)

    def test_produce_and_warehouse(self):
        self.assertEqual(produce_price("이벤트작물|5"),
                         round(10 * HARVEST_CONFIG["star_price_multipliers"][4]))
        board = build_warehouse([{"item_id": "이벤트작물_seed", "amount": 1},
                                 {"item_id": "당근_seed", "amount": 1}], 0)
        self.assertEqual(board["seeds"], [("이벤트작물", 1), ("당근", 1)])


class GenerateStockTest(unittest.TestCase):
    def test_layout_and_determinism(self):
        stock = generate_stock(7, "2026-11-12T09", 5)
        self.assertEqual(stock, generate_stock(7, "2026-11-12T09", 5))
        self.assertEqual([s["slot"] for s in stock], list(range(1, 7)))
        categories = [item_category(s["item_id"]) for s in stock]
        self.assertEqual(categories, ["seed"] * 4 + ["fertilizer"] * 2)
        ids = [s["item_id"] for s in stock]
        self.assertEqual(len(ids), len(set(ids)))
        for s in stock:
            category = item_category(s["item_id"])
            self.assertEqual(s["price"], item_price(s["item_id"]))
            self.assertEqual(
                s["stock"],
                SHOP_CONFIG["stock_by_grade"][category][item_grade(s["item_id"]) - 1],
            )

    def test_only_allowed_grades_appear(self):
        for level in (1, 3, 5, 8, 12):
            for n in range(40):
                for s in generate_stock(n, f"p{n}", level):
                    category = item_category(s["item_id"])
                    self.assertIn(item_grade(s["item_id"]), grade_odds(category, level))

    def test_higher_level_gets_better_items_on_average(self):
        def avg_grade(level):
            grades = [item_grade(s["item_id"])
                      for n in range(300) for s in generate_stock(n, "p", level)]
            return sum(grades) / len(grades)

        self.assertGreater(avg_grade(12), avg_grade(5))
        self.assertGreater(avg_grade(5), avg_grade(1))

    def test_level_1_fills_all_seed_slots(self):
        for n in range(30):
            seeds = [s for s in generate_stock(n, "p", 1)
                     if item_category(s["item_id"]) == "seed"]
            self.assertEqual(len(seeds), SHOP_CONFIG["slots"]["seed"])

    def test_falls_back_when_grade_pool_runs_out(self):
        # 허용 등급의 품목이 슬롯 수보다 적으면 남는 칸은 비운다
        small_pool = {"seed": {1: ["당근_seed"], 2: ["토마토_seed", "오이_seed"]},
                      "fertilizer": {g: [i] for i, f in FERTILIZERS.items()
                                     for g in [f["grade"]]}}
        with patch("service.shop.items_by_grade", side_effect=small_pool.get):
            seeds = [s for s in generate_stock(1, "p", 1)
                     if item_category(s["item_id"]) == "seed"]
        self.assertEqual(sorted(s["item_id"] for s in seeds),
                         ["당근_seed", "오이_seed", "토마토_seed"])


def shop_item(slot, item_id, stock=5, bought=0, price=None):
    return {"slot": slot, "item_id": item_id, "category": item_category(item_id),
            "grade": item_grade(item_id), "price": price or item_price(item_id),
            "stock": stock, "bought": bought}


def shop_board(items=None, gold=1000, level=1, reset_at=12345):
    return {"level": level, "gold": gold, "items": items or [], "reset_at": reset_at}


class ShopServiceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.repo = MagicMock(get_items=AsyncMock(), restock=AsyncMock(), buy=AsyncMock())
        self.users = MagicMock(get_user=AsyncMock(return_value={"farm_exp": 0, "gold": 300}),
                               spend_gold=AsyncMock(return_value=200))
        self.inventory = MagicMock(update_inventory=AsyncMock())
        patches = [
            patch("service.base.db", FakeDB()),
            no_passives(),
            patch("service.shop.ShopRepository", return_value=self.repo),
            patch("service.shop.UserRepository", return_value=self.users),
            patch("service.shop.InventoryRepository", return_value=self.inventory),
            patch("service.shop.shop_period", return_value=("2026-11-12T09", 999)),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    async def test_get_shop_restocks_when_empty(self):
        rows = [{"slot": 1, "item_id": "당근_seed", "price": 50, "stock": 10, "bought": 2}]
        self.repo.get_items.side_effect = [[], rows]
        board = await ShopService().get_shop(1)

        args = self.repo.restock.await_args.args
        self.assertEqual(args[:2], (1, "2026-11-12T09"))
        self.assertEqual(args[2], generate_stock(1, "2026-11-12T09", 1))
        self.assertEqual(board["level"], 1)
        self.assertEqual(board["gold"], 300)
        self.assertEqual(board["reset_at"], 999)
        self.assertEqual(board["items"][0]["category"], "seed")
        self.assertEqual(board["items"][0]["grade"], 1)

    async def test_get_shop_keeps_existing_stock(self):
        self.repo.get_items.return_value = [
            {"slot": 1, "item_id": "중급_비료", "price": 120, "stock": 3, "bought": 0},
            {"slot": 2, "item_id": "삭제된작물_seed", "price": 1, "stock": 1, "bought": 0},
        ]
        board = await ShopService().get_shop(1)
        self.repo.restock.assert_not_awaited()
        self.assertEqual([i["item_id"] for i in board["items"]], ["중급_비료"])

    async def test_buy_success(self):
        self.repo.buy.return_value = {"item_id": "당근_seed", "price": 50}
        result = await ShopService().buy(1, 2, 3)
        self.assertEqual(result, {"ok": True, "reason": None, "item_id": "당근_seed",
                                  "amount": 3, "cost": 150})
        self.repo.buy.assert_awaited_once_with(1, 2, "2026-11-12T09", 3)
        self.users.spend_gold.assert_awaited_once_with(1, 150)
        self.inventory.update_inventory.assert_awaited_once_with(1, "당근_seed", 3)

    async def test_buy_sold_out(self):
        self.repo.buy.return_value = None
        result = await ShopService().buy(1, 2, 3)
        self.assertEqual((result["ok"], result["reason"]), (False, "sold_out"))
        self.users.spend_gold.assert_not_awaited()

    async def test_buy_not_enough_gold(self):
        self.repo.buy.return_value = {"item_id": "인삼_seed", "price": 800}
        self.users.spend_gold.return_value = None
        result = await ShopService().buy(1, 2, 1)
        self.assertEqual((result["ok"], result["reason"], result["cost"]),
                         (False, "no_gold", 800))
        self.inventory.update_inventory.assert_not_awaited()

    async def test_buy_invalid_amount(self):
        result = await ShopService().buy(1, 2, 0)
        self.assertEqual(result["reason"], "invalid")
        self.repo.buy.assert_not_awaited()


class FertilizerServiceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.farm_repo = MagicMock(apply_fertilizer=AsyncMock(return_value=1500))
        self.inv_repo = MagicMock(get_item=AsyncMock(return_value={"amount": 2}),
                                  update_inventory=AsyncMock(), get_inventory=AsyncMock())
        patches = [
            patch("service.base.db", FakeDB()),
            no_passives(),
            patch("service.farm.FarmRepository", return_value=self.farm_repo),
            patch("service.farm.InventoryRepository", return_value=self.inv_repo),
            patch("service.farm.time", return_value=1000.0),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    async def test_apply_success(self):
        self.assertEqual(await FarmService().apply_fertilizer(1, 3, "중급_비료"), 1500)
        self.farm_repo.apply_fertilizer.assert_awaited_once_with(
            1, 3, "중급_비료", FERTILIZERS["중급_비료"]["reduce_minutes"] * 60, 1000
        )
        self.inv_repo.update_inventory.assert_awaited_once_with(1, "중급_비료", -1)

    async def test_apply_without_fertilizer(self):
        for owned in (None, {"amount": 0}):
            self.inv_repo.get_item.return_value = owned
            self.assertIsNone(await FarmService().apply_fertilizer(1, 3, "중급_비료"))
        self.farm_repo.apply_fertilizer.assert_not_awaited()

    async def test_apply_rejected_does_not_consume(self):
        self.farm_repo.apply_fertilizer.return_value = None  # 빈 땅/이미 사용/다 자람
        self.assertIsNone(await FarmService().apply_fertilizer(1, 3, "중급_비료"))
        self.inv_repo.update_inventory.assert_not_awaited()

    async def test_unknown_fertilizer(self):
        self.assertIsNone(await FarmService().apply_fertilizer(1, 3, "당근_seed"))
        self.inv_repo.get_item.assert_not_awaited()

    async def test_get_fertilizers(self):
        self.inv_repo.get_inventory.return_value = [
            {"item_id": "상급_비료", "amount": 1},
            {"item_id": "당근_seed", "amount": 3},
            {"item_id": "최하급_비료", "amount": 2},
            {"item_id": "중급_비료", "amount": 0},
        ]
        self.assertEqual(await FarmService().get_fertilizers(1),
                         [("최하급_비료", 2), ("상급_비료", 1)])


class FertilizerViewTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.author = make_author(1)
        self.service = MagicMock(get_fertilizers=AsyncMock(), apply_fertilizer=AsyncMock(),
                                 get_farm=AsyncMock())
        p = patch("ui.views.FarmService", return_value=self.service)
        p.start()
        self.addCleanup(p.stop)
        self.future = int(time.time()) + 3600

    def growing(self, fertilizer=None):
        s = slot(1, "수박", self.future)
        s["fertilizer"] = fertilizer
        return s

    async def test_button_state(self):
        cases = [
            (slot(1), True),  # 빈 땅
            (slot(1, "수박", 100), True),  # 이미 다 자람
            (self.growing("중급_비료"), True),  # 이미 비료 사용
            (self.growing(), False),
        ]
        for s, disabled in cases:
            with self.subTest(slot=s):
                view = FarmView([s], self.author)
                self.assertEqual(buttons_by_label(view)["비료 주기"].disabled, disabled)

    async def test_fertilized_marker(self):
        view = FarmView([self.growing("중급_비료")], self.author)
        self.assertIn("🧪 · ", view.create_embed().description.splitlines()[-1])
        self.assertIn("🧪", selects(view)[0].options[0].label)

    async def test_button_without_fertilizer(self):
        self.service.get_fertilizers.return_value = []
        interaction = make_interaction()
        await FarmView([self.growing()], self.author).fertilizer_button_callback(interaction)
        kwargs = interaction.response.send_message.await_args.kwargs
        self.assertEqual(kwargs["delete_after"], NOTICE_DELETE_AFTER)
        self.assertIn("/상점", interaction.response.send_message.await_args.args[0])

    async def test_button_opens_select(self):
        self.service.get_fertilizers.return_value = [("최하급_비료", 2), ("상급_비료", 1)]
        interaction = make_interaction()
        await FarmView([self.growing()], self.author).fertilizer_button_callback(interaction)
        view = interaction.response.send_message.await_args.kwargs["view"]
        self.assertIsInstance(view, FertilizerSelectView)
        options = selects(view)[0].options
        self.assertEqual([o.value for o in options], ["최하급_비료", "상급_비료"])
        self.assertIn("1분 단축", options[0].description)
        self.assertIn("보유 2개", options[0].description)

    async def test_select_apply_success(self):
        self.service.apply_fertilizer.return_value = 4321
        self.service.get_farm.return_value = [self.growing("상급_비료")]
        farm_message = MagicMock(edit=AsyncMock())
        view = FertilizerSelectView(FarmView([self.growing()], self.author), farm_message, 1,
                                    [("상급_비료", 1)])
        interaction = make_interaction(values=["상급_비료"])
        await view.select_callback(interaction)

        self.service.apply_fertilizer.assert_awaited_once_with(1, 1, "상급_비료")
        kwargs = interaction.response.edit_message.await_args.kwargs
        self.assertIn("10분 단축", kwargs["content"])
        self.assertIn("<t:4321:R>", kwargs["content"])
        self.assertEqual(kwargs["delete_after"], NOTICE_DELETE_AFTER)
        new_view = farm_message.edit.await_args.kwargs["view"]
        self.assertTrue(buttons_by_label(new_view)["비료 주기"].disabled)

    async def test_select_apply_failure(self):
        self.service.apply_fertilizer.return_value = None
        farm_message = MagicMock(edit=AsyncMock())
        view = FertilizerSelectView(FarmView([self.growing()], self.author), farm_message, 1,
                                    [("상급_비료", 1)])
        interaction = make_interaction(values=["상급_비료"])
        await view.select_callback(interaction)
        self.assertIn("줄 수 없습니다",
                      interaction.response.edit_message.await_args.kwargs["content"])
        farm_message.edit.assert_not_awaited()


class ShopEmbedTest(unittest.TestCase):
    def odds(self, level=1):
        return {c: grade_odds(c, level) for c in ("seed", "fertilizer")}

    def test_embed(self):
        board = shop_board([shop_item(1, "수박_seed", stock=4, bought=1),
                            shop_item(2, "중급_비료", stock=3, bought=3)], gold=777, level=5)
        embed = embed_shop(board, "tester", self.odds(5), selected=1)
        self.assertEqual(embed.title, "🏪 상점")
        self.assertIn("777원", embed.description)
        self.assertIn("<t:12345:R>", embed.description)
        seed, fert, odds = embed.fields
        self.assertEqual(seed.name, "▶ 🌱 수박 씨앗 · 상급")
        self.assertIn("재고 3/4", seed.value)
        self.assertIn("20분", seed.value)  # 수박 성장 시간
        self.assertTrue(fert.name.startswith("~~"))  # 품절
        self.assertIn("품절", fert.value)
        self.assertIn("5분 단축", fert.value)
        self.assertIn("레벨 5", odds.name)
        self.assertNotIn("최하급", odds.value.split("\n")[1])  # 레벨 5 비료에 최하급 없음

    def test_empty(self):
        embed = embed_shop(shop_board(), "t", self.odds())
        self.assertIn("진열된 품목이 없습니다", embed.fields[0].value)


class ShopViewTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.author = make_author(1)
        self.service = MagicMock(buy=AsyncMock(), get_shop=AsyncMock())
        p = patch("ui.views.ShopService", return_value=self.service)
        p.start()
        self.addCleanup(p.stop)
        self.items = [
            shop_item(1, "당근_seed", stock=10, bought=10),  # 품절
            shop_item(2, "토마토_seed", stock=8, bought=1, price=100),  # 남은 7
            shop_item(3, "중급_비료", stock=3, price=120),
        ]

    async def test_default_selection_skips_sold_out(self):
        view = ShopView(self.author, shop_board(self.items))
        self.assertEqual(view.selected, 2)
        self.assertEqual([o.value for o in selects(view)[0].options if o.default], ["2"])
        self.assertEqual(selects(view)[0].options[0].description, "품절")

    async def test_buttons_by_gold_and_stock(self):
        cases = [
            # (골드, 선택, 1개/5개/최대 비활성화, 최대 개수)
            (1000, 2, (False, False, False), 7),  # 재고 7
            (250, 2, (False, True, False), 2),  # 골드로 2개
            (50, 2, (True, True, True), 0),
            (1000, 1, (True, True, True), 0),  # 품절
            (1000, 3, (False, True, False), 3),
        ]
        for gold, selected, disabled, max_amount in cases:
            with self.subTest(gold=gold, selected=selected):
                view = ShopView(self.author, shop_board(self.items, gold=gold), selected)
                b = buttons_by_label(view)
                self.assertEqual(view.max_amount(), max_amount)
                self.assertEqual(
                    (b["1개 구매"].disabled, b["5개 구매"].disabled,
                     b[f"최대 구매 ({max_amount}개)"].disabled),
                    disabled,
                )

    async def test_select_rerenders(self):
        view = ShopView(self.author, shop_board(self.items))
        interaction = make_interaction(values=["3"])
        await view.select_callback(interaction)
        new_view = interaction.response.edit_message.await_args.kwargs["view"]
        self.assertEqual(new_view.selected, 3)
        self.service.get_shop.assert_not_awaited()

    async def test_buy_success(self):
        self.service.buy.return_value = {"ok": True, "reason": None, "item_id": "토마토_seed",
                                         "amount": 5, "cost": 500}
        self.service.get_shop.return_value = shop_board(self.items, gold=500)
        view = ShopView(self.author, shop_board(self.items), 2)
        interaction = make_interaction()
        await buttons_by_label(view)["5개 구매"].callback(interaction)

        self.service.buy.assert_awaited_once_with(1, 2, 5)
        new_view = interaction.response.edit_message.await_args.kwargs["view"]
        self.assertEqual((new_view.selected, new_view.board["gold"]), (2, 500))
        assert_notice(self, interaction, "토마토 씨앗 x5 구매 완료")

    async def test_buy_max(self):
        self.service.buy.return_value = {"ok": True, "reason": None, "item_id": "토마토_seed",
                                         "amount": 2, "cost": 200}
        self.service.get_shop.return_value = shop_board(self.items, gold=50)
        view = ShopView(self.author, shop_board(self.items, gold=250), 2)
        await buttons_by_label(view)["최대 구매 (2개)"].callback(make_interaction())
        self.service.buy.assert_awaited_once_with(1, 2, 2)

    async def test_buy_failures(self):
        for reason, text in (("no_gold", "골드가 부족"), ("sold_out", "재고가 부족")):
            with self.subTest(reason=reason):
                self.service.buy.return_value = {"ok": False, "reason": reason,
                                                 "item_id": None, "amount": 1, "cost": 100}
                self.service.get_shop.return_value = shop_board(self.items)
                interaction = make_interaction()
                await ShopView(self.author, shop_board(self.items), 2).buy(interaction, 1)
                assert_notice(self, interaction, text)

    async def test_interaction_check(self):
        view = ShopView(self.author, shop_board(self.items))
        self.assertFalse(await view.interaction_check(make_interaction(2)))

    async def test_shop_command(self):
        from cogs.shop import Shop

        with patch("cogs.shop.ShopService") as service:
            service.return_value.get_shop = AsyncMock(return_value=shop_board(self.items))
            interaction = make_interaction(1)
            interaction.user = self.author
            await Shop.shop.callback(Shop(bot=None), interaction)
        kwargs = interaction.response.send_message.await_args.kwargs
        self.assertIsInstance(kwargs["view"], ShopView)
        self.assertEqual(kwargs["embed"].title, "🏪 상점")


# ------------------------------------------------------- quality / warehouse


class ProduceIdTest(unittest.TestCase):
    def test_make_and_parse(self):
        self.assertEqual(make_produce_id("당근", 3), "당근|3")
        self.assertEqual(make_produce_id("당근", 5, "mutant"), "당근|5|M")
        self.assertEqual(make_produce_id("당근", 5, "golden"), "당근|5|G")
        self.assertEqual(parse_produce_id("당근|3"), ("당근", 3, None))
        self.assertEqual(parse_produce_id("수박|5|M"), ("수박", 5, "mutant"))
        self.assertEqual(parse_produce_id("수박|2|G"), ("수박", 2, "golden"))
        self.assertEqual(parse_produce_id("당근"), ("당근", 1, None))  # 예전 형식

    def test_non_produce(self):
        for item_id in ("당근_seed", "중급_비료", "없는작물|3", "당근|0", "당근|6",
                        "당근|x", "당근|3|X", "당근|3|M|1"):
            self.assertIsNone(parse_produce_id(item_id), item_id)

    def test_price(self):
        base = CROPS_DATA["당근"]["sell_price"]
        multipliers = HARVEST_CONFIG["star_price_multipliers"]
        for stars in range(1, 6):
            self.assertEqual(produce_price(f"당근|{stars}"),
                             round(base * multipliers[stars - 1]))
        mutant = HARVEST_CONFIG["mutation"]["price_multiplier"]
        self.assertEqual(produce_price("당근|3|M"), round(base * multipliers[2] * mutant))
        self.assertEqual(produce_price("당근"), base)
        self.assertEqual(produce_price("당근_seed"), 0)
        self.assertEqual(produce_price("중급_비료"), 0)

    def test_display(self):
        self.assertEqual(stars_text(2), "★★☆☆☆")
        self.assertEqual(produce_display_name("당근|2"), "당근 ★★☆☆☆")
        self.assertEqual(produce_display_name("당근|5|M"), "🧬돌연변이 당근 ★★★★★")


class HarvestConfigTest(unittest.TestCase):
    def test_config(self):
        multipliers = HARVEST_CONFIG["star_price_multipliers"]
        self.assertEqual(len(multipliers), 5)
        self.assertEqual(multipliers, sorted(multipliers))
        self.assertEqual(multipliers[0], 1.0)
        rows = HARVEST_CONFIG["star_weights"]
        points = [r["min_points"] for r in rows]
        self.assertEqual(points, sorted(set(points)))
        for row in rows:
            self.assertEqual(len(row["weights"]), 5)
            self.assertGreater(sum(row["weights"]), 0)
        self.assertEqual(len(HARVEST_CONFIG["care_points"]["difficulty_penalty"]), 3)

    def test_rot_time_by_grade(self):
        # 최하급 12시간 · 하급 6시간 · 중급 3시간 · 상급 1시간 · 최상급 30분
        # 난이도 배율: 쉬움 x1 · 보통 x0.75 · 어려움 x0.5 · 없음(0) x1
        grades = {1: 12 * 3600, 2: 6 * 3600, 3: 3 * 3600, 4: 3600, 5: 1800}
        multipliers = {0: 1.0, 1: 1.0, 2: 0.75, 3: 0.5}
        for grade, base in grades.items():
            for difficulty, mult in multipliers.items():
                crop = {**CROPS_DATA["당근"], "buy_grade": grade, "difficulty": difficulty}
                seconds = round(base * mult)
                with self.subTest(grade=grade, difficulty=difficulty), \
                        patch.dict(CROPS_DATA, {"테스트작물": crop}):
                    self.assertEqual(rot_after_seconds("테스트작물"), seconds)
                    self.assertFalse(is_rotten("테스트작물", 100, 100 + seconds - 1))
                    self.assertTrue(is_rotten("테스트작물", 100, 100 + seconds))
        event = {**CROPS_DATA["당근"], "buy_grade": 0, "difficulty": 3}
        with patch.dict(CROPS_DATA, {"테스트작물": event}):
            self.assertEqual(rot_after_seconds("테스트작물"),
                             round(HARVEST_CONFIG["rot"]["event_after_minutes"] * 60 * 0.5))

    def test_rotten_plant_item(self):
        self.assertIsNone(parse_produce_id(ROTTEN_ITEM))  # 작물 수확물이 아님 (제작/도감 제외)
        self.assertEqual(produce_price(ROTTEN_ITEM), HARVEST_CONFIG["rot"]["sell_price"])
        self.assertEqual(produce_price(ROTTEN_ITEM, 50), HARVEST_CONFIG["rot"]["sell_price"])
        self.assertEqual(produce_display_name(ROTTEN_ITEM), "🥀 썩은 식물")
        board = build_warehouse([{"item_id": ROTTEN_ITEM, "amount": 3}], gold=0)
        self.assertEqual([(p["item_id"], p["amount"]) for p in board["produce"]],
                         [(ROTTEN_ITEM, 3)])

    def test_every_crop_has_difficulty(self):
        for name, data in CROPS_DATA.items():
            self.assertIn(data["difficulty"], (0, 1, 2, 3), name)  # 0 = 없음(감점 없음)

    def test_no_difficulty_has_no_penalty(self):
        cfg = HARVEST_CONFIG["care_points"]
        with patch.dict(CROPS_DATA, {"테스트작물": {**CROPS_DATA["당근"], "difficulty": 0}}):
            free = care_points("테스트작물", False, None, 0, 0)
        self.assertEqual(free, cfg["fresh_bonus"])
        with patch.dict(CROPS_DATA, {"테스트작물": {**CROPS_DATA["당근"], "difficulty": 3}}):
            hard = care_points("테스트작물", False, None, 0, 0)
        self.assertEqual(free - hard, cfg["difficulty_penalty"][2])

    def test_more_points_means_better_stars(self):
        expected = [sum(s * p for s, p in star_odds(points).items()) / 100
                    for points in range(-8, 15)]
        self.assertEqual(expected, sorted(expected))
        self.assertGreater(expected[-1], expected[0] + 2)


class CarePointsTest(unittest.TestCase):
    """care_points(작물, 물, 비료, 수확 가능 시각, 수확 시각)"""

    def test_each_factor(self):
        cfg = HARVEST_CONFIG["care_points"]
        grow = CROPS_DATA["당근"]["grow_time"]  # 쉬움
        base = care_points("당근", False, None, 1000, 1000)  # 제때 수확
        self.assertEqual(base, cfg["fresh_bonus"])
        self.assertEqual(care_points("당근", True, None, 1000, 1000) - base, cfg["watered"])
        self.assertEqual(care_points("당근", False, "중급_비료", 1000, 1000) - base, 3)
        self.assertEqual(care_points("당근", False, "중급_황금_비료", 1000, 1000) - base,
                         3 + FERTILIZERS["중급_황금_비료"]["quality_bonus"])
        # 수확 타이밍: 바로 > 조금 늦음 > 오래 방치
        fresh = care_points("당근", False, None, 1000, 1000 + int(grow * 0.4))
        normal = care_points("당근", False, None, 1000, 1000 + int(grow * 0.8))
        late = care_points("당근", False, None, 1000, 1000 + grow * 2)
        self.assertEqual((fresh, normal, late),
                         (cfg["fresh_bonus"], 0, -cfg["late_penalty"]))

    def test_difficulty(self):
        self.assertEqual([CROPS_DATA[c]["difficulty"] for c in ("당근", "토마토", "인삼")],
                         [1, 2, 3])
        easy = care_points("당근", True, None, 0, 0)
        normal = care_points("토마토", True, None, 0, 0)
        hard = care_points("인삼", True, None, 0, 0)
        self.assertGreater(easy, normal)
        self.assertGreater(normal, hard)

    def test_good_care_gives_better_stars(self):
        def avg_stars(**care):
            rng = random.Random(1)
            return sum(roll_quality(rng=rng, **care)[0] for _ in range(2000)) / 2000

        neglected = avg_stars(crop="인삼", watered=False, fertilizer=None,
                              ready_at=0, harvested_at=10**6)
        cared = avg_stars(crop="인삼", watered=True, fertilizer="최상급_황금_비료",
                          ready_at=0, harvested_at=0)
        self.assertGreater(cared, neglected + 1.5)

    def test_tutorial_quest_is_reasonably_achievable(self):
        # 당근 + 물 + 하급 황금 비료 + 제때 수확 → ★3 이상 80% 이상
        points = care_points("당근", True, "하급_황금_비료", 0, 0)
        self.assertGreaterEqual(sum(p for s, p in star_odds(points).items() if s >= 3), 80)

    def test_mutation(self):
        cfg = HARVEST_CONFIG["mutation"]
        self.assertEqual(mutation_chance(False, None), cfg["base_chance"])
        self.assertGreater(mutation_chance(True, "최상급_비료"), mutation_chance(True, None))
        rng = random.Random(7)
        rolls = [roll_quality("당근", True, "최상급_황금_비료", 0, 0, rng)[1]
                 for _ in range(5000)]
        self.assertAlmostEqual(rolls.count("mutant") / len(rolls),
                               mutation_chance(True, "최상급_황금_비료"), delta=0.01)

    def test_star_weights_lookup(self):
        rows = HARVEST_CONFIG["star_weights"]
        self.assertEqual(star_weights(-100), rows[0]["weights"])
        self.assertEqual(star_weights(100), rows[-1]["weights"])


class StarQuestTest(unittest.TestCase):
    def test_min_stars_matching(self):
        quest = {"objective": {"type": "harvest", "target": None, "min_stars": 3, "count": 1}}
        self.assertFalse(objective_matches(quest, "harvest", "당근", 2))
        self.assertTrue(objective_matches(quest, "harvest", "당근", 3))
        self.assertTrue(objective_matches(quest, "harvest", "당근", 5))
        self.assertFalse(objective_matches(quest, "harvest", "당근"))  # 별 정보 없음
        fruit = {"objective": {"type": "harvest", "target": None, "crop_type": "과일",
                               "min_stars": 3, "count": 1}}
        self.assertTrue(objective_matches(fruit, "harvest", "사과", 4))
        self.assertFalse(objective_matches(fruit, "harvest", "당근", 4))

    def test_star_quests_exist(self):
        def star_quests(quests):
            return [q for q in quests if q["objective"].get("min_stars")]

        self.assertTrue(star_quests(MAIN_QUESTS))
        self.assertTrue(star_quests(QUEST_POOL["daily"]))
        self.assertTrue(star_quests(QUEST_POOL["weekly"]))

    def test_tutorial_gives_quality_fertilizer_before_star_quest(self):
        ids = [q["id"] for q in MAIN_QUESTS]
        star_index = next(i for i, q in enumerate(MAIN_QUESTS)
                          if q["objective"].get("min_stars"))
        earlier = {item for q in MAIN_QUESTS[:star_index] for item in q["reward"]["items"]}
        self.assertTrue(any(FERTILIZERS[i].get("quality_bonus")
                            for i in earlier if i in FERTILIZERS))
        self.assertLess(star_index, ids.index("main_06"))  # 튜토리얼 구간

    def test_min_stars_schema(self):
        for quests in (MAIN_QUESTS, QUEST_POOL["daily"], QUEST_POOL["weekly"]):
            for quest in quests:
                obj = quest["objective"]
                if obj.get("min_stars") is not None:
                    self.assertEqual(obj["type"], "harvest", quest["id"])
                    self.assertIn(obj["min_stars"], range(1, 6))

    def test_star_objective_text(self):
        quest = QUEST_INDEX["daily"]["daily_fruit_quality3_2"]
        [field] = embed_quests(make_board("daily", quests=[make_status(quest, 1)]), "t").fields
        self.assertIn("과일 ★3 이상 1/2회 수확", field.value)


class StarQuestProgressTest(unittest.IsolatedAsyncioTestCase):
    async def test_harvest_stars_are_checked(self):
        repo = make_quest_repo()
        repo.get_claimed_ids.return_value = main_ids_before("main_04b")
        repo.get_quests.side_effect = lambda uid, t, p: (
            [quest_row("daily_quality2_3")] if t == "daily" else [])
        users = MagicMock(get_user_exp=AsyncMock(return_value=0))
        tutorial_stars = QUEST_INDEX["main"]["main_04b"]["objective"]["min_stars"]
        daily_stars = QUEST_INDEX["daily"]["daily_quality2_3"]["objective"]["min_stars"]

        def expected(stars):
            ids = set()
            if stars >= tutorial_stars:
                ids.add("main_04b")
            if stars >= daily_stars:
                ids.add("daily_quality2_3")
            return ids

        with patch("service.quest.QuestRepository", return_value=repo), \
                patch("service.quest.UserRepository", return_value=users):
            for stars in range(1, 6):
                with self.subTest(stars=stars):
                    repo.add_progress.reset_mock()
                    await record_quest_progress(MagicMock(), 1, "harvest", "당근",
                                                stars=stars)
                    self.assertEqual(
                        {c.args[2] for c in repo.add_progress.await_args_list},
                        expected(stars),
                    )
            # 별 1개는 어떤 별 퀘스트에도 해당하지 않는다
            self.assertEqual(expected(1), set())


class WarehouseBuildTest(unittest.TestCase):
    def test_classifies_and_sorts(self):
        rows = [{"item_id": "당근|2", "amount": 3}, {"item_id": "수박|5|M", "amount": 1},
                {"item_id": "당근", "amount": 2}, {"item_id": "당근_seed", "amount": 4},
                {"item_id": "인삼_seed", "amount": 1}, {"item_id": "상급_비료", "amount": 1},
                {"item_id": "최하급_비료", "amount": 2}, {"item_id": "토마토|1", "amount": 0},
                {"item_id": "알수없음", "amount": 9}]
        board = build_warehouse(rows, gold=55)
        self.assertEqual(board["gold"], 55)
        self.assertEqual([p["item_id"] for p in board["produce"]],
                         ["수박|5|M", "당근|2", "당근"])  # 비싼 순, 0개 제외
        self.assertEqual(board["produce"][0]["price"], produce_price("수박|5|M"))
        self.assertEqual(board["seeds"], [("당근", 4), ("인삼", 1)])
        self.assertEqual(board["fertilizers"], [("최하급_비료", 2), ("상급_비료", 1)])


class WarehouseServiceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.inventory = MagicMock(get_inventory=AsyncMock(return_value=[]),
                                   remove_item=AsyncMock(return_value=True))
        self.users = MagicMock(get_user=AsyncMock(return_value={"gold": 10}),
                               update_user_gold=AsyncMock())
        patches = [
            patch("service.base.db", FakeDB()),
            no_passives(),
            patch("service.warehouse.InventoryRepository", return_value=self.inventory),
            patch("service.warehouse.UserRepository", return_value=self.users),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    async def test_sell(self):
        result = await WarehouseService().sell(1, "당근|3", 2)
        price = produce_price("당근|3")
        self.assertEqual(result, {"ok": True, "sold": 2, "earned": price * 2})
        self.inventory.remove_item.assert_awaited_once_with(1, "당근|3", 2)
        self.users.update_user_gold.assert_awaited_once_with(1, price * 2)

    async def test_sell_not_enough(self):
        self.inventory.remove_item.return_value = False
        self.assertFalse((await WarehouseService().sell(1, "당근|3", 99))["ok"])
        self.users.update_user_gold.assert_not_awaited()

    async def test_cannot_sell_seeds_or_fertilizer(self):
        for item_id in ("당근_seed", "중급_비료", "없는것"):
            self.assertFalse((await WarehouseService().sell(1, item_id, 1))["ok"])
        self.assertFalse((await WarehouseService().sell(1, "당근|3", 0))["ok"])
        self.inventory.remove_item.assert_not_awaited()

    async def test_sell_all_only_produce(self):
        self.inventory.get_inventory.return_value = [
            {"item_id": "당근|3", "amount": 2}, {"item_id": "수박|1|M", "amount": 1},
            {"item_id": "당근_seed", "amount": 5}, {"item_id": "중급_비료", "amount": 1}]
        result = await WarehouseService().sell_all(1)
        earned = produce_price("당근|3") * 2 + produce_price("수박|1|M")
        self.assertEqual(result, {"ok": True, "sold": 3, "earned": earned})
        self.assertEqual({c.args[1] for c in self.inventory.remove_item.await_args_list},
                         {"당근|3", "수박|1|M"})
        self.users.update_user_gold.assert_awaited_once_with(1, earned)

    async def test_sell_all_nothing(self):
        result = await WarehouseService().sell_all(1)
        self.assertEqual(result, {"ok": False, "sold": 0, "earned": 0})
        self.users.update_user_gold.assert_not_awaited()

    async def test_get_warehouse(self):
        self.inventory.get_inventory.return_value = [{"item_id": "당근|3", "amount": 2}]
        board = await WarehouseService().get_warehouse(1)
        self.assertEqual((board["gold"], len(board["produce"])), (10, 1))


def make_warehouse(n_produce=3, gold=100):
    crops = list(CROPS_DATA)[:n_produce]
    rows = [{"item_id": f"{c}|3", "amount": 2} for c in crops]
    rows += [{"item_id": "당근_seed", "amount": 1}, {"item_id": "하급_황금_비료", "amount": 2}]
    return build_warehouse(rows, gold)


class WarehouseViewTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.author = make_author(1)
        self.service = MagicMock(sell=AsyncMock(), sell_all=AsyncMock(),
                                 get_warehouse=AsyncMock(return_value=make_warehouse()))
        p = patch("ui.views.WarehouseService", return_value=self.service)
        p.start()
        self.addCleanup(p.stop)

    async def test_embed(self):
        board = make_warehouse(gold=777)
        embed = WarehouseView(self.author, board).create_embed()
        self.assertEqual(embed.title, "📦 창고")
        self.assertIn("777원", embed.description)
        total = sum(p["price"] * p["amount"] for p in board["produce"])
        self.assertIn(f"{total}원", embed.description)
        produce, seeds, fert = embed.fields
        self.assertTrue(produce.value.startswith("▶"))
        self.assertIn("★★★☆☆ x2", produce.value)
        self.assertIn("당근 x1", seeds.value)
        self.assertIn("하급 황금 비료 x2", fert.value)

    async def test_empty(self):
        view = WarehouseView(self.author, build_warehouse([], 0))
        self.assertEqual([type(c) for c in view.children], [GameNavSelect])
        self.assertIn("수확한 작물이 없습니다", view.create_embed().fields[0].value)

    async def test_pagination(self):
        board = make_warehouse(25)
        view = WarehouseView(self.author, board)
        self.assertEqual(view.total_pages, 3)
        self.assertEqual(len(selects(view)[0].options), 10)
        interaction = make_interaction()
        await buttons_by_label(view)["다음 ▶"].callback(interaction)
        page2 = interaction.response.edit_message.await_args.kwargs["view"]
        self.assertEqual(page2.page, 2)
        self.assertEqual(page2.selected, board["produce"][10]["item_id"])

    async def test_buttons(self):
        labels = set(buttons_by_label(WarehouseView(self.author, make_warehouse())))
        self.assertIn("1개 판매", labels)
        self.assertIn("전부 판매 (2개)", labels)
        self.assertTrue(any(label.startswith("수확물 모두 판매") for label in labels))

    async def test_sell_one(self):
        board = make_warehouse()
        item = board["produce"][1]
        self.service.sell.return_value = {"ok": True, "sold": 1, "earned": item["price"]}
        view = WarehouseView(self.author, board, selected=item["item_id"])
        interaction = make_interaction()
        await buttons_by_label(view)["1개 판매"].callback(interaction)
        self.service.sell.assert_awaited_once_with(1, item["item_id"], 1)
        interaction.response.edit_message.assert_awaited_once()
        assert_notice(self, interaction, f"+{item['price']}원")

    async def test_sell_stack(self):
        board = make_warehouse()
        item = board["produce"][0]
        self.service.sell.return_value = {"ok": True, "sold": 2, "earned": item["price"] * 2}
        view = WarehouseView(self.author, board)
        await buttons_by_label(view)["전부 판매 (2개)"].callback(make_interaction())
        self.service.sell.assert_awaited_once_with(1, item["item_id"], 2)

    async def test_sell_all(self):
        self.service.sell_all.return_value = {"ok": True, "sold": 6, "earned": 999}
        interaction = make_interaction()
        await WarehouseView(self.author, make_warehouse()).sell_all_callback(interaction)
        assert_notice(self, interaction, "+999원")

    async def test_sell_failure(self):
        self.service.sell.return_value = {"ok": False, "sold": 0, "earned": 0}
        interaction = make_interaction()
        await WarehouseView(self.author, make_warehouse()).sell(interaction, 5)
        assert_notice(self, interaction, "판매할 수 없습니다")

    async def test_select(self):
        board = make_warehouse()
        interaction = make_interaction(values=[board["produce"][2]["item_id"]])
        await WarehouseView(self.author, board).select_callback(interaction)
        new_view = interaction.response.edit_message.await_args.kwargs["view"]
        self.assertEqual(new_view.selected, board["produce"][2]["item_id"])
        self.service.get_warehouse.assert_not_awaited()

    async def test_interaction_check_and_command(self):
        view = WarehouseView(self.author, make_warehouse())
        self.assertFalse(await view.interaction_check(make_interaction(2)))

        from cogs.warehouse import Warehouse

        with patch("cogs.warehouse.WarehouseService") as service:
            service.return_value.get_warehouse = AsyncMock(return_value=make_warehouse())
            interaction = make_interaction(1)
            interaction.user = self.author
            await Warehouse.warehouse.callback(Warehouse(bot=None), interaction)
        self.assertIsInstance(
            interaction.response.send_message.await_args.kwargs["view"], WarehouseView)


# ------------------------------------------------- golden / passive / craft


class GoldenCropTest(unittest.TestCase):
    def test_price_and_display(self):
        base = produce_price("당근|3")
        self.assertEqual(produce_price("당근|3|G"),
                         round(base * HARVEST_CONFIG["golden"]["price_multiplier"]))
        self.assertGreater(produce_price("당근|3|G"), produce_price("당근|3|M"))
        self.assertEqual(produce_display_name("당근|3|G"), "✨황금 당근 ★★★☆☆")

    def test_sell_bonus(self):
        self.assertEqual(produce_price("당근|1", 10), round(CROPS_DATA["당근"]["sell_price"] * 1.1))

    def test_golden_is_rarer_than_mutation(self):
        self.assertLess(golden_chance(), mutation_chance(False, None))

    def test_roll_rates_and_exclusive(self):
        rng = random.Random(3)
        effects = {"golden_chance": 0.05, "mutation_chance": 0.05}
        rolls = [roll_quality("당근", True, None, 0, 0, rng, effects)[1] for _ in range(20000)]
        self.assertAlmostEqual(rolls.count("golden") / len(rolls), golden_chance(0.05),
                               delta=0.01)
        self.assertAlmostEqual(rolls.count("mutant") / len(rolls),
                               mutation_chance(True, None, 0.05), delta=0.01)
        self.assertEqual(set(rolls), {None, "mutant", "golden"})

    def test_care_points_bonus(self):
        self.assertEqual(care_points("당근", False, None, 0, 0, bonus=2),
                         care_points("당근", False, None, 0, 0) + 2)
        rng = random.Random(1)
        with patch("service.produce.care_points", wraps=care_points) as cp:
            roll_quality("당근", False, None, 0, 0, rng, {"care_points": 2})
        self.assertEqual(cp.call_args.args[-1], 2)


class PassiveDataTest(unittest.TestCase):
    def test_nodes(self):
        branches = {b["id"] for b in PASSIVE_CONFIG["branches"]}
        ids = [n["id"] for n in PASSIVE_CONFIG["nodes"]]
        self.assertEqual(len(ids), len(set(ids)))
        for node in PASSIVE_CONFIG["nodes"]:
            with self.subTest(node=node["id"]):
                self.assertIn(node["branch"], branches)
                self.assertIn(node["effect"], EFFECT_TEXT)
                if node.get("extra_effect"):
                    self.assertIn(node["extra_effect"], EFFECT_TEXT)
                self.assertGreater(node["max_rank"], 0)
                self.assertGreater(node["per_rank"], 0)
                for req in node["requires"]:
                    self.assertIn(req["node"], PASSIVE_NODES)
                    self.assertLessEqual(req["rank"], PASSIVE_NODES[req["node"]]["max_rank"])
                self.assertLessEqual(len(node["description"]), 100)

    def test_requested_effects_exist(self):
        effects = {n["effect"] for n in PASSIVE_CONFIG["nodes"]}
        effects |= {n["extra_effect"] for n in PASSIVE_CONFIG["nodes"] if n.get("extra_effect")}
        for effect in ("sell_price_pct", "shop_discount_pct", "golden_chance",
                       "mutation_chance", "craft_material_pct"):
            self.assertIn(effect, effects)

    def test_tree_has_no_cycles_and_every_node_reachable(self):
        ranks = {}
        for _ in range(len(PASSIVE_NODES) + 1):  # 위상 순서대로 전부 최대로 찍기
            for node in PASSIVE_CONFIG["nodes"]:
                if is_unlocked(node, ranks):
                    ranks[node["id"]] = node["max_rank"]
        self.assertEqual(set(ranks), set(PASSIVE_NODES))

    def test_select_fits(self):
        self.assertLessEqual(len(PASSIVE_NODES), 25)


class PassiveLogicTest(unittest.TestCase):
    def test_compute_effects(self):
        effects = compute_effects({"trade_sell": 3, "trade_sell_2": 1, "craft_master": 2,
                                   "farm_golden": 1, "없는노드": 5})
        self.assertEqual(effects["sell_price_pct"], 15 + 10)
        self.assertEqual(effects["craft_material_pct"], 10)
        self.assertEqual(effects["craft_gold_pct"], 40)
        self.assertAlmostEqual(effects["golden_chance"], 0.005)
        self.assertEqual(compute_effects({}), {})

    def test_effect_caps(self):
        with patch.dict(PASSIVE_NODES, {"x": {"id": "x", "effect": "shop_discount_pct",
                                             "per_rank": 99, "max_rank": 5}}):
            self.assertEqual(compute_effects({"x": 5})["shop_discount_pct"], 50)

    def test_rank_over_max_is_clamped(self):
        self.assertEqual(compute_effects({"trade_sell": 99})["sell_price_pct"], 15)

    def test_points_and_cost(self):
        self.assertEqual(total_points(1), 0)
        self.assertEqual(total_points(5), 4)
        self.assertEqual(reset_cost(5), 5 * PASSIVE_CONFIG["reset_cost_per_level"])

    def test_unlock(self):
        node = PASSIVE_NODES["trade_discount"]
        self.assertFalse(is_unlocked(node, {}))
        self.assertFalse(is_unlocked(node, {"trade_sell": 1}))
        self.assertTrue(is_unlocked(node, {"trade_sell": 2}))

    def test_discount(self):
        self.assertEqual(apply_discount(100, 0), 100)
        self.assertEqual(apply_discount(100, 15), 85)
        self.assertEqual(apply_discount(1, 50), 1)  # 최소 1원

    def test_board(self):
        board = PassiveService._build_board(400, 1000, {"trade_sell": 2})  # 레벨 3
        self.assertEqual((board["level"], board["total_points"], board["spent_points"],
                          board["available_points"]), (3, 2, 2, 0))
        status = {s["node"]["id"]: s for s in board["nodes"]}
        self.assertTrue(status["trade_discount"]["unlocked"])
        self.assertFalse(status["trade_sell_2"]["unlocked"])
        self.assertEqual(board["effects"], {"sell_price_pct": 10})


class PassiveServiceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.repo = MagicMock(get_ranks=AsyncMock(return_value={}),
                              lock_user=AsyncMock(return_value={"farm_exp": 400, "gold": 5000}),
                              add_rank=AsyncMock(return_value=1), reset=AsyncMock())
        self.users = MagicMock(get_user=AsyncMock(return_value={"farm_exp": 400, "gold": 5000}),
                               spend_gold=AsyncMock(return_value=100))
        patches = [
            patch("service.base.db", FakeDB()),
            patch("service.passive.PassiveRepository", return_value=self.repo),
            patch("service.passive.UserRepository", return_value=self.users),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    async def test_invest(self):
        self.assertIsNone(await PassiveService().invest(1, "trade_sell"))
        self.repo.lock_user.assert_awaited_once_with(1)
        self.repo.add_rank.assert_awaited_once_with(1, "trade_sell", 3)

    async def test_invest_errors(self):
        self.assertEqual(await PassiveService().invest(1, "없는노드"), "unknown")
        self.assertEqual(await PassiveService().invest(1, "trade_discount"), "locked")
        self.repo.add_rank.return_value = None
        self.assertEqual(await PassiveService().invest(1, "trade_sell"), "maxed")
        self.repo.get_ranks.return_value = {"trade_sell": 2}  # 레벨 3 → 2포인트 모두 사용
        self.assertEqual(await PassiveService().invest(1, "trade_discount"), "no_points")

    async def test_reset(self):
        self.repo.get_ranks.return_value = {"trade_sell": 1}
        self.assertEqual(await PassiveService().reset(1), (True, reset_cost(3)))
        self.users.spend_gold.assert_awaited_once_with(1, reset_cost(3))
        self.repo.reset.assert_awaited_once_with(1)

    async def test_reset_no_gold_or_nothing(self):
        self.assertEqual(await PassiveService().reset(1), (False, 0))
        self.repo.get_ranks.return_value = {"trade_sell": 1}
        self.users.spend_gold.return_value = None
        self.assertEqual(await PassiveService().reset(1), (False, reset_cost(3)))
        self.repo.reset.assert_not_awaited()

    async def test_get_board(self):
        board = await PassiveService().get_board(1)
        self.assertEqual((board["level"], board["available_points"]), (3, 2))


class PassiveEffectsAppliedTest(unittest.IsolatedAsyncioTestCase):
    """패시브가 판매/상점에 실제로 반영되는지"""

    async def test_sell_bonus_applied(self):
        inventory = MagicMock(remove_item=AsyncMock(return_value=True),
                              get_inventory=AsyncMock(return_value=[]))
        users = MagicMock(update_user_gold=AsyncMock(),
                          get_user=AsyncMock(return_value={"gold": 0}))
        with patch("service.base.db", FakeDB()), no_passives({"trade_sell": 2}), \
                patch("service.warehouse.InventoryRepository", return_value=inventory), \
                patch("service.warehouse.UserRepository", return_value=users):
            result = await WarehouseService().sell(1, "당근|1", 2)
        self.assertEqual(result["earned"], produce_price("당근|1", 10) * 2)

    async def test_shop_discount_applied(self):
        repo = MagicMock(buy=AsyncMock(return_value={"item_id": "당근_seed", "price": 100}))
        users = MagicMock(spend_gold=AsyncMock(return_value=0))
        inventory = MagicMock(update_inventory=AsyncMock())
        with patch("service.base.db", FakeDB()), no_passives({"trade_sell": 2, "trade_discount": 3}), \
                patch("service.shop.ShopRepository", return_value=repo), \
                patch("service.shop.UserRepository", return_value=users), \
                patch("service.shop.InventoryRepository", return_value=inventory):
            result = await ShopService().buy(1, 1, 2)
        self.assertEqual(result["cost"], 85 * 2)
        users.spend_gold.assert_awaited_once_with(1, 170)


class RecipeDataTest(unittest.TestCase):
    def test_recipes(self):
        ids = [r["id"] for r in RECIPES]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertLessEqual(len(RECIPES), 25)
        for recipe in RECIPES:
            with self.subTest(recipe=recipe["id"]):
                out = recipe["output"]["item"]
                known = out in FERTILIZERS or (out.endswith("_seed")
                                               and out.removesuffix("_seed") in CROPS_DATA)
                self.assertTrue(known, out)
                self.assertEqual(recipe["name"], item_display_name(out))
                self.assertGreaterEqual(recipe["gold"], 0)
                for spec in recipe["ingredients"]:
                    self.assertGreater(spec["count"], 0)
                    if spec.get("crop"):
                        self.assertIn(spec["crop"], CROPS_DATA)
                    if spec.get("crop_type"):
                        self.assertIn(spec["crop_type"], {t.name for t in CropType})
                    if spec.get("variant"):
                        self.assertIn(spec["variant"], ("mutant", "golden"))
                    if spec.get("min_stars"):
                        self.assertIn(spec["min_stars"], range(1, 6))
                    # 재료로 쓸 수 있는 작물이 실제로 있어야 함
                    stars = spec.get("min_stars") or 1
                    self.assertTrue(any(
                        craft_matches(spec, make_produce_id(c, stars, spec.get("variant")))
                        for c in CROPS_DATA))

    def test_every_fertilizer_is_craftable(self):
        outputs = {r["output"]["item"] for r in RECIPES}
        self.assertTrue(set(FERTILIZERS) <= outputs)

    def test_uses_golden_crops(self):
        self.assertTrue(any(s.get("variant") == "golden"
                            for r in RECIPES for s in r["ingredients"]))


class CraftLogicTest(unittest.TestCase):
    R1 = {"ingredients": [{"count": 4}], "gold": 0}

    def test_reduced_count(self):
        self.assertEqual([reduced_count(10, p) for p in (0, 10, 25, 50)], [10, 9, 7, 5])
        self.assertEqual(reduced_count(3, 30), 2)
        self.assertEqual(reduced_count(1, 60), 1)  # 최소 1
        self.assertEqual(reduced_gold(100, 20), 80)

    def test_matches(self):
        self.assertTrue(craft_matches({"count": 1}, "당근|1"))
        self.assertFalse(craft_matches({"count": 1}, "당근|1|M"))  # 귀한 작물 보호
        self.assertFalse(craft_matches({"count": 1}, "당근_seed"))
        self.assertTrue(craft_matches({"variant": "golden", "count": 1}, "수박|2|G"))
        self.assertFalse(craft_matches({"variant": "golden", "count": 1}, "수박|2|M"))
        self.assertTrue(craft_matches({"crop_type": "과일", "min_stars": 3, "count": 1}, "사과|4"))
        self.assertFalse(craft_matches({"crop_type": "과일", "min_stars": 3, "count": 1}, "사과|2"))
        self.assertFalse(craft_matches({"crop": "사과", "count": 1}, "배|5"))

    def test_plan_uses_cheapest_first(self):
        stock = {"당근|5": 3, "당근|1": 2, "수박|1": 5}
        self.assertEqual(plan_consumption(self.R1, stock, 0), {"당근|1": 2, "당근|5": 2})
        self.assertEqual(stock["당근|1"], 2)  # 원본 유지

    def test_plan_fails_without_materials(self):
        self.assertIsNone(plan_consumption(self.R1, {"당근|1": 3, "수박|2|G": 9}, 0))

    def test_max_and_many(self):
        stock = {"당근|1": 10}
        self.assertEqual(max_crafts(self.R1, stock, 0), 2)
        self.assertEqual(max_crafts(self.R1, stock, 30), 3)  # 4 → 3개
        self.assertEqual(plan_many(self.R1, stock, 0, 2), {"당근|1": 8})
        self.assertIsNone(plan_many(self.R1, stock, 0, 3))

    def test_ingredients_do_not_double_count(self):
        recipe = {"ingredients": [{"crop": "당근", "count": 2}, {"count": 2}], "gold": 0}
        self.assertIsNone(plan_consumption(recipe, {"당근|1": 3}, 0))
        self.assertEqual(plan_consumption(recipe, {"당근|1": 4}, 0), {"당근|1": 4})

    def test_board(self):
        rows = [{"item_id": "당근|1", "amount": 8}, {"item_id": "당근|3", "amount": 4}]
        board = build_craft_board(1, 5, rows, {"craft_material_pct": 25, "craft_gold_pct": 50})
        status = {s["recipe"]["id"]: s for s in board["recipes"]}
        self.assertEqual(board["material_pct"], 25)
        fert1 = status["craft_fert_1"]
        self.assertEqual(fert1["ingredients"][0]["need"], 3)
        self.assertEqual(fert1["max_crafts"], 4)
        gold1 = status["craft_gold_1"]  # ★2 이상 4개 → 3개, 골드 10 → 5
        self.assertEqual((gold1["gold"], gold1["max_crafts"]), (5, 1))  # 골드 5원 한계
        self.assertTrue(status["craft_fert_5"]["locked"])
        self.assertEqual(status["craft_fert_5"]["max_crafts"], 0)


class CraftServiceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.inventory = MagicMock(
            get_inventory=AsyncMock(return_value=[{"item_id": "당근|1", "amount": 9}]),
            remove_item=AsyncMock(return_value=True), update_inventory=AsyncMock())
        self.users = MagicMock(spend_gold=AsyncMock(return_value=0),
                               get_user=AsyncMock(return_value={"farm_exp": 0, "gold": 50}))
        self.passive = MagicMock(get_ranks=AsyncMock(return_value={}),
                                 lock_user=AsyncMock(return_value={"farm_exp": 0, "gold": 50}))
        patches = [
            patch("service.base.db", FakeDB()),
            patch("service.craft.InventoryRepository", return_value=self.inventory),
            patch("service.craft.UserRepository", return_value=self.users),
            patch("service.craft.PassiveRepository", return_value=self.passive),
            patch("service.passive.PassiveRepository", return_value=self.passive),
            patch("service.passive.CodexRepository",
                  return_value=MagicMock(get_entries=AsyncMock(return_value=[]))),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    async def test_craft(self):
        result = await CraftService().craft(1, "craft_fert_1", 2)
        self.assertEqual((result["ok"], result["item"], result["count"]), (True, "최하급_비료", 2))
        self.inventory.remove_item.assert_awaited_once_with(1, "당근|1", 8)
        self.inventory.update_inventory.assert_awaited_once_with(1, "최하급_비료", 2)
        self.users.spend_gold.assert_not_awaited()  # 골드 0 레시피

    async def test_craft_failures(self):
        self.assertEqual((await CraftService().craft(1, "없음"))["reason"], "unknown")
        self.assertEqual((await CraftService().craft(1, "craft_fert_1", 0))["reason"], "invalid")
        self.assertEqual((await CraftService().craft(1, "craft_fert_5"))["reason"], "locked")
        self.assertEqual((await CraftService().craft(1, "craft_fert_1", 3))["reason"],
                         "no_materials")
        self.users.spend_gold.return_value = None
        self.inventory.get_inventory.return_value = [{"item_id": "당근|3", "amount": 9}]
        self.assertEqual((await CraftService().craft(1, "craft_gold_1"))["reason"], "no_gold")
        self.inventory.update_inventory.assert_not_awaited()

    async def test_board(self):
        board = await CraftService().get_board(1)
        self.assertEqual((board["level"], board["gold"]), (1, 50))


def passive_board(exp=400, gold=1000, ranks=None):
    return PassiveService._build_board(exp, gold, ranks or {})


class PassiveViewTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.author = make_author(1)
        self.service = MagicMock(invest=AsyncMock(return_value=None), reset=AsyncMock(),
                                 get_board=AsyncMock(return_value=passive_board()))
        p = patch("ui.views.PassiveService", return_value=self.service)
        p.start()
        self.addCleanup(p.stop)

    async def test_embed(self):
        board = passive_board(ranks={"trade_sell": 1})
        embed = PassiveView(self.author, board).create_embed()
        self.assertEqual(embed.title, "🌳 패시브 트리")
        self.assertIn("남은 포인트 **1**", embed.description)
        names = [f.name for f in embed.fields]
        self.assertEqual(names[:3], ["💰 상인", "🌱 재배", "🔨 장인"])
        self.assertIn("🔷 **흥정의 달인** [1/3]", embed.fields[0].value)
        self.assertIn("🔒", embed.fields[0].value)
        self.assertIn("필요: 흥정의 달인 2", embed.fields[0].value)
        self.assertIn("수확물 판매가 +5%", embed.fields[-1].value)

    async def test_buttons(self):
        view = PassiveView(self.author, passive_board())
        self.assertEqual(view.selected, "trade_sell")
        b = buttons_by_label(view)
        self.assertFalse(b["포인트 투자 (남은 2)"].disabled)
        self.assertTrue(b["초기화"].disabled)  # 찍은 게 없음
        locked = PassiveView(self.author, passive_board(), "trade_discount")
        self.assertTrue(buttons_by_label(locked)["포인트 투자 (남은 2)"].disabled)
        no_points = PassiveView(self.author, passive_board(exp=0))
        self.assertTrue(buttons_by_label(no_points)["포인트 투자 (남은 0)"].disabled)

    async def test_invest(self):
        view = PassiveView(self.author, passive_board())
        interaction = make_interaction()
        await view.invest_callback(interaction)
        self.service.invest.assert_awaited_once_with(1, "trade_sell")
        assert_notice(self, interaction, "흥정의 달인")
        self.service.invest.return_value = "locked"
        interaction = make_interaction()
        await view.invest_callback(interaction)
        assert_notice(self, interaction, "선행 패시브")

    async def test_reset_requires_confirmation(self):
        board = passive_board(ranks={"trade_sell": 1})
        view = PassiveView(self.author, board)
        interaction = make_interaction()
        await buttons_by_label(view)["초기화"].callback(interaction)
        confirm_view = interaction.response.edit_message.await_args.kwargs["view"]
        self.service.reset.assert_not_awaited()
        labels = set(buttons_by_label(confirm_view))
        self.assertIn(f"정말 초기화 ({board['reset_cost']}원)", labels)
        self.assertIn("취소", labels)

        self.service.reset.return_value = (True, board["reset_cost"])
        interaction = make_interaction()
        await confirm_view.reset_callback(interaction)
        assert_notice(self, interaction, "초기화했습니다")

        interaction = make_interaction()
        await confirm_view.cancel_reset_callback(interaction)
        back = interaction.response.edit_message.await_args.kwargs["view"]
        self.assertFalse(back.confirm_reset)

    async def test_command(self):
        from cogs.passive import Passive

        with patch("cogs.passive.PassiveService") as service:
            service.return_value.get_board = AsyncMock(return_value=passive_board())
            interaction = make_interaction(1)
            interaction.user = self.author
            await Passive.passive.callback(Passive(bot=None), interaction)
        self.assertIsInstance(
            interaction.response.send_message.await_args.kwargs["view"], PassiveView)


class CraftViewTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.author = make_author(1)
        rows = [{"item_id": "당근|1", "amount": 9}]
        self.board = build_craft_board(2, 100, rows, {})
        self.service = MagicMock(craft=AsyncMock(), get_board=AsyncMock(return_value=self.board))
        p = patch("ui.views.CraftService", return_value=self.service)
        p.start()
        self.addCleanup(p.stop)

    async def test_embed_and_default(self):
        view = CraftView(self.author, self.board)
        self.assertEqual(view.selected, "craft_fert_1")  # 만들 수 있는 첫 레시피
        embed = view.create_embed()
        self.assertEqual(embed.title, "🔨 제작")
        ready, short, locked, detail = embed.fields
        self.assertEqual(ready.name, "✅ 제작 가능 (1)")
        self.assertIn("▶ **최하급 비료** — 2회 가능", ready.value)
        self.assertTrue(short.name.startswith("📦 재료 부족"))
        self.assertIn("• 하급 비료 — 재료 부족", short.value)
        self.assertTrue(locked.name.startswith("🔒 잠김"))
        self.assertIn("중급 비료 Lv.3", locked.value)
        levels = [int(x) for x in re.findall(r"Lv\.(\d+)", locked.value)]
        self.assertEqual(levels, sorted(levels))  # 낮은 레벨부터
        self.assertIn("✅ 아무 작물 x4 (보유 9)", detail.value)
        self.assertIn("➡️ 최하급 비료 x1", detail.value)

    async def test_buttons(self):
        b = buttons_by_label(CraftView(self.author, self.board))
        self.assertFalse(b["1회 제작"].disabled)
        self.assertFalse(b["최대 제작 (2회)"].disabled)
        b = buttons_by_label(CraftView(self.author, self.board, "craft_fert_5"))
        self.assertTrue(b["1회 제작"].disabled)

    async def test_craft(self):
        self.service.craft.return_value = {"ok": True, "crafted": 2, "reason": None,
                                           "item": "최하급_비료", "count": 2, "gold": 0}
        view = CraftView(self.author, self.board)
        interaction = make_interaction()
        await buttons_by_label(view)["최대 제작 (2회)"].callback(interaction)
        self.service.craft.assert_awaited_once_with(1, "craft_fert_1", 2)
        assert_notice(self, interaction, "최하급 비료 x2 제작 완료")

    async def test_craft_failure(self):
        self.service.craft.return_value = {"ok": False, "reason": "no_materials"}
        interaction = make_interaction()
        await CraftView(self.author, self.board).craft(interaction, 1)
        assert_notice(self, interaction, "재료가 부족")

    async def test_command(self):
        from cogs.craft import Craft

        with patch("cogs.craft.CraftService") as service:
            service.return_value.get_board = AsyncMock(return_value=self.board)
            interaction = make_interaction(1)
            interaction.user = self.author
            await Craft.craft.callback(Craft(bot=None), interaction)
        self.assertIsInstance(
            interaction.response.send_message.await_args.kwargs["view"], CraftView)


class LevelUpNoticeTest(unittest.IsolatedAsyncioTestCase):
    async def test_harvest_level_up(self):
        service = MagicMock(get_farm=AsyncMock(return_value=[slot(1)]), harvest_crop=AsyncMock(
            return_value={"crop": "당근", "stars": 2, "variant": "golden", "mutant": False,
                          "golden": True, "item_id": "당근|2|G", "level_up": 4}))
        with patch("ui.views.FarmService", return_value=service):
            interaction = make_interaction()
            await FarmView([slot(1, "당근", 100)], make_author(1)).harvest_button_callback(
                interaction)
        msg = interaction.followup.send.await_args.args[0]
        self.assertIn("✨황금 당근", msg)
        self.assertIn("황금 작물 발견", msg)
        self.assertIn("레벨 업! 농장 레벨 4", msg)
        self.assertIn("/패시브", msg)

    async def test_quest_claim_level_up(self):
        quest = QUEST_INDEX["main"]["main_06"]
        service = MagicMock(claim_rewards=AsyncMock(return_value=[quest]),
                            get_quests=AsyncMock(return_value=make_board(level=3)))
        with patch("ui.views.QuestService", return_value=service):
            interaction = make_interaction()
            await QuestView(make_author(1), make_board(level=2)).claim_callback(interaction)
        self.assertIn("레벨 업! 농장 레벨 3", interaction.followup.send.await_args.args[0])


# --------------------------------------------------------- command mention


class CommandMentionTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        p = patch.dict("utils.command_mention._COMMAND_IDS", clear=True)
        p.start()
        self.addCleanup(p.stop)

    def test_mention_after_sync(self):
        from utils.command_mention import command_mention, register_commands

        self.assertEqual(command_mention("퀘스트"), "`/퀘스트`")  # 동기화 전
        register_commands([SimpleNamespace(name="퀘스트", id=1234),
                           SimpleNamespace(name="농사", id=5678)])
        self.assertEqual(command_mention("퀘스트"), "</퀘스트:1234>")
        self.assertEqual(command_mention("농사"), "</농사:5678>")
        self.assertEqual(command_mention("없는명령어"), "`/없는명령어`")

    async def test_register_success_points_to_quest_command(self):
        from cogs.user import User
        from utils.command_mention import register_commands

        register_commands([SimpleNamespace(name="퀘스트", id=42)])
        with patch("cogs.user.UserService") as service:
            service.return_value.create_user = AsyncMock(return_value={"user_id": 1})
            interaction = make_interaction()
            await User.register.callback(User(bot=None), interaction)
        msg = interaction.response.send_message.await_args.args[0]
        self.assertIn("회원가입에 성공했습니다", msg)
        self.assertIn("</퀘스트:42>", msg)

    async def test_messages_use_mentions(self):
        from utils.command_mention import register_commands

        register_commands([SimpleNamespace(name=n, id=i) for i, n in
                           enumerate(["회원가입", "패시브", "상점", "창고", "제작"], start=1)])
        self.assertIn("</회원가입:1>", UserNotRegistered(1).message)
        service = MagicMock(get_fertilizers=AsyncMock(return_value=[]))
        with patch("ui.views.FarmService", return_value=service):
            interaction = make_interaction()
            future = int(time.time()) + 600
            await FarmView([slot(1, "당근", future)], make_author(1)) \
                .fertilizer_button_callback(interaction)
        self.assertIn("</상점:3>", interaction.response.send_message.await_args.args[0])
        from ui.views import level_up_text

        self.assertIn("</패시브:2>", level_up_text(3))

    async def test_help_command_lists_public_commands(self):
        from cogs.help import Help, build_help_embed, is_admin_command, public_commands
        from utils.command_mention import register_commands

        bot = commands.Bot(command_prefix="!", intents=discord.Intents.default())
        for filename in sorted(os.listdir("cogs")):
            if filename.endswith(".py") and not filename.startswith("_"):
                await bot.load_extension(f"cogs.{filename[:-3]}")
        self.addAsyncCleanup(bot.close)

        all_names = {c.name for c in bot.tree.get_commands()}
        public = {c.name for c in public_commands(bot.tree)}
        self.assertIn("아이템넣기", all_names)
        self.assertNotIn("아이템넣기", public)  # 어드민 전용 제외
        self.assertEqual(public, all_names - {"아이템넣기", "정적데이터재로드"})
        self.assertTrue(is_admin_command(bot.tree.get_command("아이템넣기")))

        register_commands([SimpleNamespace(name=n, id=100 + i)
                           for i, n in enumerate(sorted(all_names))])
        embed = build_help_embed(public_commands(bot.tree))
        text = "\n".join(f.value for f in embed.fields)
        for name in public:
            self.assertIn(f"</{name}:", text)  # 클릭 가능한 멘션
        self.assertNotIn("아이템넣기", text)
        self.assertNotIn("…", text)  # 모든 명령어에 설명이 있음
        self.assertEqual(embed.fields[0].name, "🚀 시작하기")
        self.assertTrue(embed.fields[0].value.startswith("</회원가입:"))
        self.assertNotIn("📦 기타", [f.name for f in embed.fields])  # 모두 분류됨

        interaction = make_interaction()
        await Help.help.callback(bot.get_cog("Help"), interaction)
        kwargs = interaction.response.send_message.await_args.kwargs
        self.assertTrue(kwargs["ephemeral"])
        self.assertEqual(kwargs["embed"].title, "📖 명령어 목록")

    def test_uncategorized_commands_go_to_other(self):
        from cogs.help import build_help_embed

        command = SimpleNamespace(name="새명령어", description="새 기능")
        embed = build_help_embed([command])
        self.assertEqual(embed.fields[-1].name, "📦 기타")
        self.assertIn("새 기능", embed.fields[-1].value)

    async def test_register_failure(self):
        from cogs.user import User

        with patch("cogs.user.UserService") as service:
            service.return_value.create_user = AsyncMock(return_value=None)
            interaction = make_interaction()
            await User.register.callback(User(bot=None), interaction)
        msg = interaction.response.send_message.await_args.args[0]
        self.assertIn("실패", msg)
        self.assertNotIn("퀘스트", msg)


# -------------------------------------------------------------------- codex


def codex_rows(*records):
    return [{"crop_id": c, "variant": v, "harvest_count": n, "best_stars": b}
            for c, v, n, b in records]


class CodexLogicTest(unittest.TestCase):
    def test_variant_mapping(self):
        self.assertEqual(codex_variant(None), "normal")
        self.assertEqual(codex_variant("golden"), "golden")
        self.assertEqual(CODEX_VARIANTS, ("normal", "golden", "mutant"))

    def test_crop_order(self):
        crops = codex_crops()
        self.assertEqual(set(crops), {c for c in CROPS_DATA if not is_event_crop(c)})
        grades = [CROPS_DATA[c]["buy_grade"] for c in crops]
        self.assertEqual(grades, sorted(grades))

    def test_event_crops_are_not_in_codex(self):
        event = {**CROPS_DATA["당근"], "buy_grade": EVENT_GRADE}
        with patch.dict(CROPS_DATA, {"이벤트작물": event}):
            self.assertNotIn("이벤트작물", codex_crops())
            rows = codex_rows(("이벤트작물", "normal", 5, 3), ("당근", "normal", 1, 1))
            self.assertEqual(codex_counts(rows)["normal"], 1)  # 보상 진행도에도 안 셈
            board = build_codex(rows, "normal")
            self.assertNotIn("이벤트작물", [e["crop"] for e in board["entries"]])
            self.assertEqual(board["total"], len(codex_crops()))

    def test_build_separates_variants(self):
        rows = codex_rows(("당근", "normal", 12, 4), ("당근", "golden", 1, 2),
                          ("수박", "mutant", 3, 5), ("없는작물", "normal", 1, 1),
                          ("당근", "이상한값", 1, 1))
        normal = build_codex(rows, "normal")
        self.assertEqual(normal["total"], len(codex_crops()))
        self.assertEqual(normal["discovered"], 1)
        self.assertEqual(normal["progress"],
                         {"normal": (1, len(codex_crops())), "golden": (1, len(codex_crops())),
                          "mutant": (1, len(codex_crops()))})
        entry = next(e for e in normal["entries"] if e["crop"] == "당근")
        self.assertEqual(entry, {"crop": "당근", "discovered": True,
                                 "harvest_count": 12, "best_stars": 4})
        self.assertFalse(next(e for e in normal["entries"] if e["crop"] == "수박")["discovered"])

        mutant = build_codex(rows, "mutant")
        self.assertTrue(next(e for e in mutant["entries"] if e["crop"] == "수박")["discovered"])
        self.assertFalse(next(e for e in mutant["entries"] if e["crop"] == "당근")["discovered"])

    def test_embed(self):
        board = build_codex(codex_rows(("당근", "normal", 12, 4)), "normal")
        embed = embed_codex(board, "tester")
        self.assertEqual(embed.title, "📖 작물 도감 · 일반")
        self.assertIn(f"발견 **1/{len(codex_crops())}**", embed.description)
        self.assertIn("황금 0/", embed.description)
        # 페이지의 작물을 등급별 필드로 묶고, 미발견은 이름 없이 개수만
        first = embed.fields[0]
        self.assertEqual(first.name, f"〔{grade_name(1)}〕")
        self.assertIn("✅ **당근** ★★★★☆ · 12회", first.value)
        self.assertIn(f"❔ 미발견 {CODEX_PAGE_SIZE - 1}종", first.value)
        self.assertNotIn("상추", first.value)
        self.assertEqual(embed.footer.text, f"페이지 1/{codex_total_pages(board)}")
        golden_board = build_codex([], "golden")
        golden = embed_codex(golden_board, "t", page=99)  # 범위 밖 → 마지막 페이지
        self.assertEqual(golden.title, "✨ 작물 도감 · 황금")
        last = codex_total_pages(golden_board)
        self.assertTrue(golden.footer.text.startswith(f"페이지 {last}/{last}"))
        self.assertIn("따로 등록", golden.footer.text)


class CodexViewTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.author = make_author(1)
        self.service = MagicMock(get_codex=AsyncMock(
            return_value=build_codex(codex_rows(("수박", "golden", 1, 3)), "golden")))
        p = patch("ui.views.CodexService", return_value=self.service)
        p.start()
        self.addCleanup(p.stop)
        self.board = build_codex(codex_rows(("당근", "normal", 2, 3)), "normal")

    async def test_tabs(self):
        view = CodexView(self.author, self.board)
        b = buttons_by_label(view)
        total = len(codex_crops())
        self.assertTrue(b[f"일반 (1/{total})"].disabled)
        self.assertFalse(b[f"황금 (0/{total})"].disabled)
        self.assertFalse(b[f"돌연변이 (0/{total})"].disabled)

        interaction = make_interaction()
        await b[f"황금 (0/{total})"].callback(interaction)
        self.service.get_codex.assert_awaited_once_with(1, "golden")
        kwargs = interaction.response.edit_message.await_args.kwargs
        self.assertEqual(kwargs["view"].board["variant"], "golden")
        self.assertEqual(kwargs["embed"].title, "✨ 작물 도감 · 황금")

    async def test_pages(self):
        view = CodexView(self.author, self.board)
        b = buttons_by_label(view)
        self.assertTrue(b["◀ 이전"].disabled)
        interaction = make_interaction()
        await b["다음 ▶"].callback(interaction)
        self.assertEqual(interaction.response.edit_message.await_args.kwargs["view"].page, 2)
        self.service.get_codex.assert_not_awaited()
        last = CodexView(self.author, self.board, page=999)
        self.assertEqual(last.page, codex_total_pages(self.board))
        self.assertTrue(buttons_by_label(last)["다음 ▶"].disabled)

    async def test_interaction_check_and_command(self):
        self.assertFalse(await CodexView(self.author, self.board)
                         .interaction_check(make_interaction(2)))
        from cogs.codex import Codex

        with patch("cogs.codex.CodexService") as service:
            service.return_value.get_codex = AsyncMock(return_value=self.board)
            interaction = make_interaction(1)
            interaction.user = self.author
            await Codex.codex.callback(Codex(bot=None), interaction)
        service.return_value.get_codex.assert_awaited_once_with(1)
        self.assertIsInstance(
            interaction.response.send_message.await_args.kwargs["view"], CodexView)

    async def test_harvest_notice_new_codex(self):
        service = MagicMock(get_farm=AsyncMock(return_value=[slot(1)]), harvest_crop=AsyncMock(
            return_value={"crop": "수박", "stars": 3, "variant": "mutant", "mutant": True,
                          "golden": False, "item_id": "수박|3|M", "level_up": None,
                          "new_codex": True}))
        with patch("ui.views.FarmService", return_value=service):
            interaction = make_interaction()
            await FarmView([slot(1, "수박", 100)], self.author).harvest_button_callback(
                interaction)
        self.assertIn("돌연변이 도감에 수박 신규 등록", interaction.followup.send.await_args.args[0])


# ------------------------------------------------------------- codex rewards


def codex_found(variant, n):
    """variant 도감에 작물 n 종을 발견한 행"""
    return [{"crop_id": c, "variant": variant, "harvest_count": 1, "best_stars": 1}
            for c in codex_crops()[:n]]


class CodexRewardDataTest(unittest.TestCase):
    def test_rewards(self):
        self.assertEqual(set(CODEX_REWARDS), set(CODEX_VARIANTS))
        for variant, milestones in CODEX_REWARDS.items():
            counts = [m["count"] for m in milestones]
            self.assertEqual(counts, sorted(set(counts)), variant)
            for m in milestones:
                with self.subTest(variant=variant, count=m["count"]):
                    self.assertIn(m["effect"], EFFECT_TEXT)
                    self.assertGreater(m["value"], 0)
                    self.assertLessEqual(m["count"], len(CROPS_DATA))  # 달성 가능

    def test_requested_buffs_exist(self):
        effects = {m["effect"] for ms in CODEX_REWARDS.values() for m in ms}
        self.assertIn("sell_price_pct", effects)  # 작물 가격
        self.assertIn("grow_time_pct", effects)  # 생장 속도


class CodexRewardLogicTest(unittest.TestCase):
    def test_counts(self):
        rows = codex_found("normal", 3) + codex_found("golden", 1) + [
            {"crop_id": "없는작물", "variant": "normal", "harvest_count": 1, "best_stars": 1}]
        self.assertEqual(codex_counts(rows), {"normal": 3, "golden": 1, "mutant": 0})

    def test_milestones_and_effects(self):
        first = CODEX_REWARDS["normal"][0]
        milestones = codex_milestones("normal", first["count"])
        self.assertTrue(milestones[0]["achieved"])
        self.assertFalse(milestones[1]["achieved"])
        self.assertEqual(codex_effects({"normal": first["count"] - 1}), {})
        self.assertEqual(codex_effects({"normal": first["count"]}),
                         {first["effect"]: first["value"]})

    def test_effects_stack_across_codexes(self):
        everything = {v: len(CROPS_DATA) for v in CODEX_VARIANTS}
        effects = codex_effects(everything)
        expected: dict = {}
        for ms in CODEX_REWARDS.values():
            for m in ms:
                expected[m["effect"]] = expected.get(m["effect"], 0) + m["value"]
        self.assertEqual(effects.keys(), expected.keys())
        for key in expected:
            self.assertAlmostEqual(effects[key], expected[key])

    def test_merge_with_passive_and_limit(self):
        merged = merge_effects({"sell_price_pct": 10}, {"sell_price_pct": 2, "grow_time_pct": 80})
        self.assertEqual(merged["sell_price_pct"], 12)
        self.assertEqual(merged["grow_time_pct"], 50)  # 상한

    def test_board_contains_rewards(self):
        board = build_codex(codex_found("normal", 5), "normal")
        self.assertTrue(board["milestones"][0]["achieved"])
        self.assertEqual(board["effects"], codex_effects({"normal": 5}))
        embed = embed_codex(board, "t")
        rewards = next(f for f in embed.fields if f.name.startswith("🎁"))
        self.assertTrue(rewards.value.startswith("✅ 5종"))
        self.assertIn("🎯 **15종**", rewards.value)  # 다음 목표 하나만 진행 막대와 함께
        self.assertIn("` 5/15", rewards.value)
        later = len(board["milestones"]) - 2
        self.assertIn(f"이후 보상 {later}단계 더", rewards.value)
        self.assertNotIn("30종", rewards.value)
        active = embed.fields[-1]
        self.assertIn("수확물 판매가 +2%", active.value)

    def test_reward_text_all_achieved(self):
        ms = [{"count": 1, "effect": "exp_pct", "value": 5, "achieved": True}]
        self.assertIn("🏆 모든 보상을 달성했습니다!", codex_reward_text(ms, 1))

    def test_helpers(self):
        self.assertEqual(reduced_grow_time(600, 10), 540)
        self.assertEqual(reduced_grow_time(1, 50), 1)  # 최소 1초
        self.assertEqual(bonus_exp(10, 10), 11)


class CodexBuffAppliedTest(unittest.IsolatedAsyncioTestCase):
    """도감 보상이 실제 판매/심기/수확에 반영되는지"""

    async def test_get_effects_merges_passive_and_codex(self):
        with no_passives({"trade_sell": 1}, codex_found("normal", 5)):
            effects = await get_effects(MagicMock(), 1)
        self.assertEqual(effects["sell_price_pct"], 5 + 2)

    async def test_sell_price_buff(self):
        inventory = MagicMock(remove_item=AsyncMock(return_value=True))
        users = MagicMock(update_user_gold=AsyncMock())
        with patch("service.base.db", FakeDB()), no_passives(None, codex_found("normal", 5)), \
                patch("service.warehouse.InventoryRepository", return_value=inventory), \
                patch("service.warehouse.UserRepository", return_value=users):
            result = await WarehouseService().sell(1, "당근|1", 1)
        self.assertEqual(result["earned"], produce_price("당근|1", 2))

    async def test_grow_time_buff_on_plant(self):
        farm_repo = MagicMock(check_farm_slot=AsyncMock(return_value=(SlotStatus.EMPTY, {})),
                              plant_seed=AsyncMock())
        inv = MagicMock(get_item=AsyncMock(return_value={"amount": 1}),
                        update_inventory=AsyncMock())
        with patch("service.base.db", FakeDB()), no_passives(None, codex_found("normal", 15)), \
                patch("service.farm.FarmRepository", return_value=farm_repo), \
                patch("service.farm.InventoryRepository", return_value=inv), \
                patch("service.farm.time", return_value=1000.0), \
                patch("service.farm.record_quest_progress", new_callable=AsyncMock):
            self.assertTrue(await FarmService().plant_seed(1, 1, "수박"))
        grow = CROPS_DATA["수박"]["grow_time"]
        farm_repo.plant_seed.assert_awaited_once_with(
            1, 1, "수박", 1000 + reduced_grow_time(grow, 5))

    async def test_exp_buff_on_harvest(self):
        farm_repo = MagicMock(harvest_crop=AsyncMock(return_value={
            "crop_id": "수박", "planted_at": 900, "is_watered": False, "fertilizer": None}))
        users = MagicMock(get_user_exp=AsyncMock(return_value=0), update_user_exp=AsyncMock())
        with patch("service.base.db", FakeDB()), no_passives(None, codex_found("normal", 30)), \
                patch("service.farm.FarmRepository", return_value=farm_repo), \
                patch("service.farm.InventoryRepository",
                      return_value=MagicMock(update_inventory=AsyncMock())), \
                patch("service.farm.UserRepository", return_value=users), \
                patch("service.farm.CodexRepository",
                      return_value=MagicMock(record=AsyncMock(return_value=False))), \
                patch("service.farm.time", return_value=1000.0), \
                patch("service.farm.record_quest_progress", new_callable=AsyncMock):
            await FarmService().harvest_crop(1, 1)
        users.update_user_exp.assert_awaited_once_with(
            1, bonus_exp(CROPS_DATA["수박"]["exp"], 5))


class GameNavTest(unittest.IsolatedAsyncioTestCase):
    """/게임창: 홈 화면 + 모든 게임 화면 맨 아래의 화면 이동 메뉴"""

    async def asyncSetUp(self):
        self.author = make_author(1)
        self.user = {"user_id": 1, "farm_exp": 400, "level": 3, "gold": 1234}
        boards = {
            "FarmService": ("get_farm", [slot(1, "당근", int(time.time()) + 60)]),
            "ShopService": ("get_shop", shop_board()),
            "CraftService": ("get_board", build_craft_board(2, 100, [], {})),
            "WarehouseService": ("get_warehouse", make_warehouse()),
            "CodexService": ("get_codex", build_codex([], "normal")),
            "PassiveService": ("get_board", passive_board()),
            "QuestService": ("get_quests", make_board()),
            "UserService": ("get_user", self.user),
        }
        for name, (method, board) in boards.items():
            service = MagicMock(**{method: AsyncMock(return_value=board)})
            p = patch(f"ui.views.{name}", return_value=service)
            p.start()
            self.addCleanup(p.stop)

    @staticmethod
    def nav(view):
        [nav] = [c for c in view.children if isinstance(c, GameNavSelect)]
        return nav

    async def test_every_screen_has_nav_with_current_selected(self):
        for key, name, _, _ in GAME_SCREENS:
            with self.subTest(screen=key):
                embed, view = await build_game_screen(key, self.author)
                self.assertIsInstance(embed, discord.Embed)
                nav = self.nav(view)
                self.assertEqual(nav.row, 4)
                self.assertEqual([o.value for o in nav.options], [k for k, *_ in GAME_SCREENS])
                self.assertEqual([o.value for o in nav.options if o.default], [key])
                self.assertLessEqual(max(c.row or 0 for c in view.children), 4)

    async def test_home_embed(self):
        embed, view = await build_game_screen("home", self.author)
        self.assertIsInstance(view, GameHomeView)
        self.assertIn("Lv.**3**", embed.description)
        self.assertIn("1,234원", embed.description)
        self.assertIn(level_progress(400, 3), embed.description)
        for key, name, _, _ in GAME_SCREENS[1:]:
            self.assertIn(name, embed.fields[0].value)

    async def test_select_moves_to_screen(self):
        _, home = await build_game_screen("home", self.author)
        nav = self.nav(home)
        nav._values = ["warehouse"]
        interaction = make_interaction()
        await nav.callback(interaction)
        kwargs = interaction.response.edit_message.await_args.kwargs
        self.assertIsInstance(kwargs["view"], WarehouseView)
        self.assertEqual(kwargs["embed"].title, "📦 창고")

        # 이동한 화면에서 다시 홈으로
        nav = self.nav(kwargs["view"])
        nav._values = ["home"]
        await nav.callback(interaction)
        self.assertIsInstance(
            interaction.response.edit_message.await_args.kwargs["view"], GameHomeView
        )

    async def test_screen_refresh_keeps_nav(self):
        # 화면 안에서 다시 그려도(새 View) 이동 메뉴가 유지된다
        view = ShopView(self.author, shop_board())
        self.assertEqual(self.nav(view).options[2].value, "shop")
        self.assertTrue(self.nav(view).options[2].default)

    async def test_other_user_blocked(self):
        _, home = await build_game_screen("home", self.author)
        other = make_interaction(2)
        self.assertFalse(await home.interaction_check(other))
        self.assertTrue(other.response.send_message.await_args.kwargs["ephemeral"])


# ---------------------------------------------------- integration (opt-in)


@unittest.skipUnless(os.getenv("FARMBOT_DB_TEST") == "1", "FARMBOT_DB_TEST=1 일 때만 실행")
class DatabaseIntegrationTest(unittest.IsolatedAsyncioTestCase):
    """실제 PostgreSQL 에서 SQL 과 서비스 동작을 확인. 끝나면 ROLLBACK."""

    async def asyncSetUp(self):
        import asyncpg
        import config

        self.conn = await asyncpg.connect(
            host=config.PG_HOST,
            port=config.PG_PORT,
            user=config.PG_USER,
            password=config.PG_PASSWORD,
            database=config.PG_DATABASE,
        )
        exists = await self.conn.fetchval("SELECT to_regclass('public.user_farms')")
        if exists is None:
            await self.conn.close()
            self.skipTest("테이블이 없습니다. python db_init.py 를 먼저 실행하세요.")

        self.tx = self.conn.transaction()
        await self.tx.start()

        # 새로 추가된 테이블(user_quests 등)이 아직 없으면 트랜잭션 안에서 만든다 (ROLLBACK 됨)
        schema_outdated = await self.conn.fetchval(
            """
            SELECT to_regclass('public.user_quests') IS NULL
                OR to_regclass('public.user_shop') IS NULL
                OR to_regclass('public.user_passives') IS NULL
                OR to_regclass('public.user_codex') IS NULL
                OR NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'user_farms' AND column_name = 'fertilizer')
            """
        )
        if schema_outdated:
            await self.conn.execute(SCHEMA_SQL)

        # 서비스가 같은 conn 을 쓰게 한다 (중첩 transaction() 은 SAVEPOINT 가 됨)
        conn = self.conn

        class RealConnDB:
            @asynccontextmanager
            async def connection(self):
                yield conn

            @asynccontextmanager
            async def transaction(self):
                async with conn.transaction():
                    yield conn

        p = patch("service.base.db", RealConnDB())
        p.start()
        self.addCleanup(p.stop)

        self.users = UserRepository(self.conn)
        self.farms = FarmRepository(self.conn)
        self.inventory = InventoryRepository(self.conn)
        await self.users.create_user(TEST_USER_ID)

    async def asyncTearDown(self):
        await self.tx.rollback()
        await self.conn.close()

    async def amount(self, item_id):
        row = await self.inventory.get_item(TEST_USER_ID, item_id)
        return row["amount"] if row else None

    async def test_level_up_opens_farm_slots(self):
        service = FarmService()
        self.assertEqual([r["slot_id"] for r in await service.get_farm(TEST_USER_ID)], [1])
        # 1번 땅에 심어 둔 작물은 땅이 늘어나도 그대로
        await self.conn.execute(
            "UPDATE user_farms SET crop_id = '당근', planted_at = 1 WHERE user_id = $1",
            TEST_USER_ID,
        )
        await self.users.update_user_exp(TEST_USER_ID, 1600)  # 레벨 5
        farm = sorted(await service.get_farm(TEST_USER_ID), key=lambda r: r["slot_id"])
        self.assertEqual([r["slot_id"] for r in farm], list(range(1, slots_for_level(5) + 1)))
        self.assertEqual(farm[0]["crop_id"], "당근")
        self.assertTrue(all(r["crop_id"] is None for r in farm[1:]))
        # 다시 열어도 중복으로 만들지 않는다
        self.assertEqual(await self.farms.ensure_slots(TEST_USER_ID, slots_for_level(5)), 0)
        self.assertEqual(len(await service.get_farm(TEST_USER_ID)), slots_for_level(5))

    async def test_new_user_defaults_and_trigger(self):
        user = await self.users.get_user(TEST_USER_ID)
        self.assertEqual((user["gold"], user["farm_exp"]), (100, 0))
        # 트리거가 1번 슬롯을 만들어 준다
        status, _ = await self.farms.check_farm_slot(TEST_USER_ID, 1)
        self.assertEqual(status, SlotStatus.EMPTY)
        status, _ = await self.farms.check_farm_slot(TEST_USER_ID, 2)
        self.assertEqual(status, SlotStatus.LOCKED)

    async def test_create_user_twice_returns_none(self):
        self.assertIsNone(await self.users.create_user(TEST_USER_ID))

    async def test_update_inventory_semantics(self):
        await self.inventory.update_inventory(TEST_USER_ID, "당근_seed", 2)
        self.assertEqual(await self.amount("당근_seed"), 2)
        await self.inventory.update_inventory(TEST_USER_ID, "당근_seed", -1)
        self.assertEqual(await self.amount("당근_seed"), 1)
        # 0 미만으로 내려가는 변경은 예외 없이 무시된다
        await self.inventory.update_inventory(TEST_USER_ID, "당근_seed", -5)
        self.assertEqual(await self.amount("당근_seed"), 1)
        # 없는 아이템에 음수 → 0 으로 생성
        await self.inventory.update_inventory(TEST_USER_ID, "토마토_seed", -1)
        self.assertEqual(await self.amount("토마토_seed"), 0)

    async def test_gold_and_exp_updates(self):
        await self.users.update_user_gold(TEST_USER_ID, 50)
        await self.users.update_user_exp(TEST_USER_ID, 30)
        user = await self.users.get_user(TEST_USER_ID)
        self.assertEqual((user["gold"], user["farm_exp"]), (150, 30))
        self.assertEqual(await self.users.get_user_exp(TEST_USER_ID), 30)

    async def test_plant_and_harvest_flow(self):
        service = FarmService()
        # 씨앗이 없으면 못 심는다
        self.assertFalse(await service.plant_seed(TEST_USER_ID, 1, "당근"))

        await self.inventory.update_inventory(TEST_USER_ID, "당근_seed", 1)
        self.assertEqual(await service.get_seeds(TEST_USER_ID), [("당근", 1)])
        self.assertTrue(await service.plant_seed(TEST_USER_ID, 1, "당근"))
        self.assertEqual(await self.amount("당근_seed"), 0)

        # 이미 심어진 땅에는 못 심는다
        await self.inventory.update_inventory(TEST_USER_ID, "당근_seed", 1)
        self.assertFalse(await service.plant_seed(TEST_USER_ID, 1, "당근"))

        # 아직 덜 자람 → 수확 실패, 작물 그대로
        self.assertIsNone(await service.harvest_crop(TEST_USER_ID, 1))
        status, _ = await self.farms.check_farm_slot(TEST_USER_ID, 1)
        self.assertEqual(status, SlotStatus.PLANTED)

        # 다 자란 것으로 만들고 수확
        await self.conn.execute(
            "UPDATE user_farms SET planted_at = EXTRACT(EPOCH FROM NOW())::bigint - 1 WHERE user_id = $1 AND slot_id = 1",
            TEST_USER_ID,
        )
        result = await service.harvest_crop(TEST_USER_ID, 1)
        self.assertEqual(result["crop"], "당근")
        self.assertIn(result["stars"], range(1, 6))
        self.assertEqual(result["item_id"],
                         make_produce_id("당근", result["stars"], result["variant"]))
        self.assertEqual(await self.amount(result["item_id"]), 1)
        user = await self.users.get_user(TEST_USER_ID)
        # 별은 무작위라 별 배율이 적용된 경험치(result["exp"])와 비교
        self.assertEqual(result["exp"], star_exp(CROPS_DATA["당근"]["exp"], result["stars"]))
        self.assertEqual(user["farm_exp"], result["exp"])
        status, _ = await self.farms.check_farm_slot(TEST_USER_ID, 1)
        self.assertEqual(status, SlotStatus.EMPTY)

    async def test_water_seed(self):
        service = FarmService()
        # 빈 땅에는 물을 줄 수 없음
        self.assertFalse(await service.water_seed(TEST_USER_ID, 1))

        await self.inventory.update_inventory(TEST_USER_ID, "당근_seed", 1)
        await service.plant_seed(TEST_USER_ID, 1, "당근")
        self.assertTrue(await service.water_seed(TEST_USER_ID, 1))
        farm = await service.get_farm(TEST_USER_ID)
        self.assertTrue(farm[0]["is_watered"])
        # 이미 물을 줬으면 다시 줄 수 없음 (퀘스트 중복 카운트 방지)
        self.assertFalse(await service.water_seed(TEST_USER_ID, 1))

    async def test_get_user_service_with_real_record(self):
        user = await UserService().get_user(TEST_USER_ID)
        self.assertEqual(user["level"], 1)
        embed_profile(user)  # /프로필 전체 경로

    # -- quests

    async def test_main_quest_chain(self):
        quests = QuestService()
        board = await quests.get_quests(TEST_USER_ID, "main")
        [status] = board["quests"]
        self.assertEqual(status["quest"]["id"], "main_01")
        self.assertTrue(status["completed"])

        # main_01 보상: 골드 + 씨앗
        claimed = await quests.claim_rewards(TEST_USER_ID, "main")
        self.assertEqual([q["id"] for q in claimed], ["main_01"])
        reward = MAIN_QUESTS[0]["reward"]
        user = await self.users.get_user(TEST_USER_ID)
        self.assertEqual(user["gold"], 100 + reward["gold"])
        for item_id, amount in reward["items"].items():
            self.assertEqual(await self.amount(item_id), amount)
        # 중복 수령 불가
        self.assertEqual(await quests.claim_rewards(TEST_USER_ID, "main"), [])

        # main_02: 당근 심기 → 농사 행동으로 진행도 증가
        board = await quests.get_quests(TEST_USER_ID, "main")
        self.assertEqual(board["quests"][0]["quest"]["id"], "main_02")
        self.assertEqual(board["main_cleared"], 1)
        self.assertFalse(board["quests"][0]["completed"])

        # target 이 없으면(아무 작물) main_01 보상으로 받은 당근을 심는다
        tutorial_crop = QUEST_INDEX["main"]["main_02"]["objective"]["target"] or "당근"
        self.assertGreater(await self.amount(f"{tutorial_crop}_seed"), 0)
        self.assertTrue(await FarmService().plant_seed(TEST_USER_ID, 1, tutorial_crop))
        board = await quests.get_quests(TEST_USER_ID, "main")
        self.assertTrue(board["quests"][0]["completed"])
        self.assertEqual([q["id"] for q in await quests.claim_rewards(TEST_USER_ID, "main")],
                         ["main_02"])

        # main_03: 물주기 — 앞 퀘스트를 하는 동안의 행동은 반영되지 않는다
        board = await quests.get_quests(TEST_USER_ID, "main")
        self.assertEqual(board["quests"][0]["quest"]["id"], "main_03")
        self.assertEqual(board["quests"][0]["progress"], 0)
        await FarmService().water_seed(TEST_USER_ID, 1)
        board = await quests.get_quests(TEST_USER_ID, "main")
        self.assertEqual(board["quests"][0]["progress"], 1)

    async def test_daily_and_weekly_quests(self):
        quests = QuestService()
        for quest_type, count in PERIOD_QUEST_COUNT.items():
            board = await quests.get_quests(TEST_USER_ID, quest_type)
            self.assertEqual(len(board["quests"]), count)
            # 다시 열어도 같은 퀘스트
            again = await quests.get_quests(TEST_USER_ID, quest_type)
            self.assertEqual([s["quest"]["id"] for s in board["quests"]],
                             [s["quest"]["id"] for s in again["quests"]])

        # 당근을 심고 수확하면 해당 목표의 일일/주간 진행도가 오른다
        await self.inventory.update_inventory(TEST_USER_ID, "당근_seed", 1)
        await FarmService().plant_seed(TEST_USER_ID, 1, "당근")
        await self.conn.execute(
            "UPDATE user_farms SET planted_at = EXTRACT(EPOCH FROM NOW())::bigint - 1 WHERE user_id = $1", TEST_USER_ID
        )
        # 별 조건 퀘스트가 있으므로 수확 품질을 고정 (★3)
        with patch("service.farm.roll_quality", return_value=(3, None)):
            await FarmService().harvest_crop(TEST_USER_ID, 1)

        for quest_type in PERIOD_QUEST_COUNT:
            board = await quests.get_quests(TEST_USER_ID, quest_type)
            for status in board["quests"]:
                with self.subTest(quest=status["quest"]["id"]):
                    plant = objective_matches(status["quest"], "plant", "당근")
                    harvest = objective_matches(status["quest"], "harvest", "당근", stars=3)
                    self.assertEqual(status["progress"], int(plant or harvest))

    async def test_period_quests_reset(self):
        quests = QuestService()
        repo = QuestRepository(self.conn)
        await quests.get_quests(TEST_USER_ID, "daily")
        # 지난 기간 기록으로 만들어 두면 새 기간에 다시 배정되고 옛 기록은 삭제
        await self.conn.execute(
            "UPDATE user_quests SET period = '2000-01-01', progress = 99"
            " WHERE user_id = $1 AND quest_type = 'daily'",
            TEST_USER_ID,
        )
        board = await quests.get_quests(TEST_USER_ID, "daily")
        self.assertTrue(all(s["progress"] == 0 for s in board["quests"]))
        self.assertEqual(await repo.get_quests(TEST_USER_ID, "daily", "2000-01-01"), [])

    # -- shop

    async def test_shop_restock_and_buy(self):
        shop = ShopService()
        board = await shop.get_shop(TEST_USER_ID)
        self.assertEqual(len(board["items"]), sum(SHOP_CONFIG["slots"].values()))
        self.assertEqual(board["gold"], 100)
        again = await shop.get_shop(TEST_USER_ID)
        self.assertEqual(board["items"], again["items"])

        item = min(board["items"], key=lambda i: i["price"])
        result = await shop.buy(TEST_USER_ID, item["slot"], 1)
        self.assertTrue(result["ok"])
        self.assertEqual(await self.amount(item["item_id"]), 1)
        board = await shop.get_shop(TEST_USER_ID)
        self.assertEqual(board["gold"], 100 - item["price"])
        bought = next(i for i in board["items"] if i["slot"] == item["slot"])
        self.assertEqual(bought["bought"], 1)

        # 재고보다 많이 → 품절
        result = await shop.buy(TEST_USER_ID, item["slot"], item["stock"])
        self.assertEqual(result["reason"], "sold_out")

    async def test_shop_not_enough_gold_rolls_back_stock(self):
        shop = ShopService()
        board = await shop.get_shop(TEST_USER_ID)
        await self.conn.execute("UPDATE users SET gold = 0 WHERE user_id = $1", TEST_USER_ID)
        item = board["items"][0]

        result = await shop.buy(TEST_USER_ID, item["slot"], 1)
        self.assertEqual(result["reason"], "no_gold")
        board = await shop.get_shop(TEST_USER_ID)
        self.assertEqual(board["items"][0]["bought"], 0)  # 재고 차감도 롤백
        self.assertIsNone(await self.amount(item["item_id"]))

    async def test_shop_restocks_next_period(self):
        shop = ShopService()
        await shop.get_shop(TEST_USER_ID)
        await self.conn.execute(
            "UPDATE user_shop SET period = '2000-01-01T00', bought = stock WHERE user_id = $1",
            TEST_USER_ID,
        )
        board = await shop.get_shop(TEST_USER_ID)
        self.assertTrue(all(i["bought"] == 0 for i in board["items"]))
        count = await self.conn.fetchval(
            "SELECT count(*) FROM user_shop WHERE user_id = $1", TEST_USER_ID
        )
        self.assertEqual(count, len(board["items"]))

    async def test_shop_level_changes_odds(self):
        # 레벨 12 (exp 12100) 이면 최하급 비료가 나오지 않는다
        await self.users.update_user_exp(TEST_USER_ID, 12100)
        board = await ShopService().get_shop(TEST_USER_ID)
        self.assertEqual(board["level"], 12)
        for item in board["items"]:
            self.assertIn(item["grade"], grade_odds(item["category"], 12))

    # -- fertilizer

    async def test_fertilizer_reduces_grow_time_once(self):
        farm = FarmService()
        await self.inventory.update_inventory(TEST_USER_ID, "수박_seed", 2)
        await self.inventory.update_inventory(TEST_USER_ID, "중급_비료", 2)
        self.assertTrue(await farm.plant_seed(TEST_USER_ID, 1, "수박"))
        before = (await farm.get_farm(TEST_USER_ID))[0]["planted_at"]

        ready_at = await farm.apply_fertilizer(TEST_USER_ID, 1, "중급_비료")
        self.assertEqual(ready_at, before - 5 * 60)
        self.assertEqual(await self.amount("중급_비료"), 1)
        row = (await farm.get_farm(TEST_USER_ID))[0]
        self.assertEqual((row["planted_at"], row["fertilizer"]), (ready_at, "중급_비료"))

        # 같은 재배에 두 번은 불가, 비료도 소모되지 않음
        self.assertIsNone(await farm.apply_fertilizer(TEST_USER_ID, 1, "중급_비료"))
        self.assertEqual(await self.amount("중급_비료"), 1)

        # 수확하면 초기화 → 새로 심으면 다시 사용 가능
        await self.conn.execute(
            "UPDATE user_farms SET planted_at = EXTRACT(EPOCH FROM NOW())::bigint - 1 WHERE user_id = $1", TEST_USER_ID
        )
        await farm.harvest_crop(TEST_USER_ID, 1)
        self.assertIsNone((await farm.get_farm(TEST_USER_ID))[0]["fertilizer"])
        await farm.plant_seed(TEST_USER_ID, 1, "수박")
        self.assertIsNotNone(await farm.apply_fertilizer(TEST_USER_ID, 1, "중급_비료"))

    async def test_fertilizer_cannot_go_past_now(self):
        farm = FarmService()
        await self.inventory.update_inventory(TEST_USER_ID, "당근_seed", 1)
        await self.inventory.update_inventory(TEST_USER_ID, "최상급_비료", 1)
        await farm.plant_seed(TEST_USER_ID, 1, "당근")  # 2분 30초 < 20분
        now = int(time.time())
        ready_at = await farm.apply_fertilizer(TEST_USER_ID, 1, "최상급_비료")
        self.assertGreaterEqual(ready_at, now)
        self.assertLessEqual(ready_at, now + 5)
        self.assertEqual((await farm.harvest_crop(TEST_USER_ID, 1))["crop"], "당근")

    async def test_fertilizer_on_empty_slot(self):
        await self.inventory.update_inventory(TEST_USER_ID, "중급_비료", 1)
        self.assertIsNone(await FarmService().apply_fertilizer(TEST_USER_ID, 1, "중급_비료"))
        self.assertEqual(await self.amount("중급_비료"), 1)

    # -- quality / warehouse

    async def harvest_once(self, crop, fertilizer=None, water=True, roll=(3, None)):
        """심기 → (물) → (비료) → 바로 수확. roll 로 품질을 고정"""
        farm = FarmService()
        await self.inventory.update_inventory(TEST_USER_ID, f"{crop}_seed", 1)
        self.assertTrue(await farm.plant_seed(TEST_USER_ID, 1, crop))
        if water:
            self.assertTrue(await farm.water_seed(TEST_USER_ID, 1))
        if fertilizer:
            await self.inventory.update_inventory(TEST_USER_ID, fertilizer, 1)
            self.assertIsNotNone(await farm.apply_fertilizer(TEST_USER_ID, 1, fertilizer))
        await self.conn.execute(
            "UPDATE user_farms SET planted_at = $2 WHERE user_id = $1",
            TEST_USER_ID, int(time.time()),
        )
        with patch("service.farm.roll_quality", return_value=roll) as rolled:
            result = await farm.harvest_crop(TEST_USER_ID, 1)
        return result, rolled

    async def test_harvest_passes_care_state_to_quality_roll(self):
        result, rolled = await self.harvest_once("당근", "하급_황금_비료", roll=(4, "mutant"))
        crop, watered, fertilizer, ready_at, harvested_at = rolled.call_args.args
        self.assertEqual((crop, watered, fertilizer), ("당근", True, "하급_황금_비료"))
        self.assertLessEqual(abs(harvested_at - ready_at), 5)
        self.assertEqual(result["item_id"], "당근|4|M")
        self.assertEqual(await self.amount("당근|4|M"), 1)

    async def test_sell_flow(self):
        await self.harvest_once("당근", roll=(5, None))
        await self.harvest_once("당근", roll=(5, None))
        await self.harvest_once("당근", roll=(1, "mutant"))
        warehouse = WarehouseService()
        board = await warehouse.get_warehouse(TEST_USER_ID)
        self.assertEqual({p["item_id"]: p["amount"] for p in board["produce"]},
                         {"당근|5": 2, "당근|1|M": 1})

        gold = board["gold"]
        result = await warehouse.sell(TEST_USER_ID, "당근|5", 1)
        self.assertEqual(result, {"ok": True, "sold": 1, "earned": produce_price("당근|5")})
        # 보유량보다 많이는 못 판다 (골드도 그대로)
        self.assertFalse((await warehouse.sell(TEST_USER_ID, "당근|5", 5))["ok"])
        board = await warehouse.get_warehouse(TEST_USER_ID)
        self.assertEqual(board["gold"], gold + produce_price("당근|5"))

        await self.inventory.update_inventory(TEST_USER_ID, "중급_비료", 2)
        result = await warehouse.sell_all(TEST_USER_ID)
        self.assertEqual(result["sold"], 2)
        self.assertEqual(result["earned"], produce_price("당근|5") + produce_price("당근|1|M"))
        board = await warehouse.get_warehouse(TEST_USER_ID)
        self.assertEqual(board["produce"], [])
        self.assertEqual(board["fertilizers"], [("중급_비료", 2)])  # 비료는 안 팔림

    async def test_star_quest_progress(self):
        quests = QuestService()
        target = QUEST_INDEX["main"]["main_04b"]
        min_stars = target["objective"]["min_stars"]
        await self.conn.executemany(
            "INSERT INTO user_quests (user_id, quest_type, quest_id, period, claimed)"
            " VALUES ($1, 'main', $2, '', TRUE)",
            [(TEST_USER_ID, i) for i in main_ids_before("main_04b")],
        )
        await self.harvest_once("당근", roll=(min_stars - 1, None))
        [status] = (await quests.get_quests(TEST_USER_ID, "main"))["quests"]
        self.assertEqual((status["quest"]["id"], status["progress"]), ("main_04b", 0))

        await self.harvest_once("당근", roll=(min_stars, None))
        [status] = (await quests.get_quests(TEST_USER_ID, "main"))["quests"]
        self.assertTrue(status["completed"])

    # -- passive / craft

    async def test_passive_invest_and_reset(self):
        passive = PassiveService()
        self.assertEqual(await passive.invest(TEST_USER_ID, "trade_sell"), "no_points")
        await self.users.update_user_exp(TEST_USER_ID, 400)  # 레벨 3 → 2포인트
        await self.users.update_user_gold(TEST_USER_ID, 10000)
        self.assertIsNone(await passive.invest(TEST_USER_ID, "trade_sell"))
        self.assertEqual(await passive.invest(TEST_USER_ID, "trade_discount"), "locked")
        self.assertIsNone(await passive.invest(TEST_USER_ID, "trade_sell"))
        self.assertEqual(await passive.invest(TEST_USER_ID, "trade_sell"), "no_points")
        board = await passive.get_board(TEST_USER_ID)
        self.assertEqual(board["effects"], {"sell_price_pct": 10})

        # 판매가 보너스 실제 적용
        await self.inventory.update_inventory(TEST_USER_ID, "당근|1", 1)
        result = await WarehouseService().sell(TEST_USER_ID, "당근|1", 1)
        self.assertEqual(result["earned"], produce_price("당근|1", 10))

        ok, cost = await passive.reset(TEST_USER_ID)
        self.assertTrue(ok)
        board = await passive.get_board(TEST_USER_ID)
        self.assertEqual((board["spent_points"], board["available_points"]), (0, 2))

    async def test_passive_max_rank(self):
        await self.users.update_user_exp(TEST_USER_ID, 10**6)
        passive = PassiveService()
        for _ in range(3):
            self.assertIsNone(await passive.invest(TEST_USER_ID, "trade_sell"))
        self.assertEqual(await passive.invest(TEST_USER_ID, "trade_sell"), "maxed")

    async def test_shop_discount(self):
        await self.users.update_user_exp(TEST_USER_ID, 10**6)
        for node in ("trade_sell", "trade_sell", "trade_discount", "trade_discount"):
            self.assertIsNone(await PassiveService().invest(TEST_USER_ID, node))
        board = await ShopService().get_shop(TEST_USER_ID)
        self.assertEqual(board["discount_pct"], 10)
        item = board["items"][0]
        self.assertEqual(item["price"], apply_discount(item_price(item["item_id"]), 10))
        await self.users.update_user_gold(TEST_USER_ID, 100000)
        result = await ShopService().buy(TEST_USER_ID, item["slot"], 1)
        self.assertEqual(result["cost"], item["price"])

    async def test_craft_flow(self):
        await self.inventory.update_inventory(TEST_USER_ID, "당근|1", 5)
        await self.inventory.update_inventory(TEST_USER_ID, "당근|4|G", 1)  # 보호 대상
        craft = CraftService()
        board = await craft.get_board(TEST_USER_ID)
        status = next(s for s in board["recipes"] if s["recipe"]["id"] == "craft_fert_1")
        self.assertEqual(status["max_crafts"], 1)

        result = await craft.craft(TEST_USER_ID, "craft_fert_1")
        self.assertTrue(result["ok"])
        self.assertEqual(await self.amount("최하급_비료"), 1)
        self.assertEqual(await self.amount("당근|1"), 1)
        self.assertEqual(await self.amount("당근|4|G"), 1)  # 황금 작물은 그대로
        self.assertEqual((await craft.craft(TEST_USER_ID, "craft_fert_1"))["reason"],
                         "no_materials")

    async def test_craft_no_gold_rolls_back(self):
        await self.conn.execute("UPDATE users SET gold = 0 WHERE user_id = $1", TEST_USER_ID)
        await self.inventory.update_inventory(TEST_USER_ID, "당근|3", 4)
        result = await CraftService().craft(TEST_USER_ID, "craft_gold_1")
        self.assertEqual(result["reason"], "no_gold")
        self.assertEqual(await self.amount("당근|3"), 4)  # 재료 그대로

    async def test_craft_material_passive(self):
        await self.users.update_user_exp(TEST_USER_ID, 10**6)
        for _ in range(3):
            await PassiveService().invest(TEST_USER_ID, "craft_saving")  # -30%
        await self.inventory.update_inventory(TEST_USER_ID, "당근|1", 3)
        self.assertTrue((await CraftService().craft(TEST_USER_ID, "craft_fert_1"))["ok"])
        self.assertEqual(await self.amount("당근|1"), 0)  # 4 → 3개만 사용

    async def test_harvest_level_up_and_golden(self):
        await self.users.update_user_exp(TEST_USER_ID, 95)  # 레벨 1, 당근 10xp 로 레벨 2
        result, _ = await self.harvest_once("당근", roll=(2, "golden"))
        self.assertEqual(result["level_up"], 2)
        self.assertTrue(result["golden"])
        self.assertEqual(await self.amount("당근|2|G"), 1)

    async def test_codex_records_per_variant(self):
        codex = CodexService()
        board = await codex.get_codex(TEST_USER_ID)
        self.assertEqual(board["discovered"], 0)

        first, _ = await self.harvest_once("당근", roll=(2, None))
        self.assertTrue(first["new_codex"])
        again, _ = await self.harvest_once("당근", roll=(4, None))
        self.assertFalse(again["new_codex"])  # 같은 변종은 한 번만 신규
        golden, _ = await self.harvest_once("당근", roll=(1, "golden"))
        self.assertTrue(golden["new_codex"])  # 황금 도감은 따로

        normal = await codex.get_codex(TEST_USER_ID, "normal")
        entry = next(e for e in normal["entries"] if e["crop"] == "당근")
        self.assertEqual((entry["harvest_count"], entry["best_stars"]), (2, 4))
        self.assertEqual(normal["progress"]["golden"][0], 1)
        self.assertEqual(normal["progress"]["mutant"][0], 0)
        golden_board = await codex.get_codex(TEST_USER_ID, "golden")
        entry = next(e for e in golden_board["entries"] if e["crop"] == "당근")
        self.assertEqual((entry["harvest_count"], entry["best_stars"]), (1, 1))
        self.assertEqual((await codex.get_codex(TEST_USER_ID, "이상한값"))["variant"], "normal")

    async def test_codex_buff_applies_to_planting(self):
        # 일반 도감 15종 → 성장 시간 -5%
        for crop in codex_crops()[:15]:
            await self.conn.execute(
                "INSERT INTO user_codex (user_id, crop_id, variant, harvest_count, best_stars)"
                " VALUES ($1, $2, 'normal', 1, 1)", TEST_USER_ID, crop)
        effects = await get_effects(self.conn, TEST_USER_ID)
        self.assertEqual(effects["grow_time_pct"], 5)
        await self.inventory.update_inventory(TEST_USER_ID, "수박_seed", 1)
        before = int(time.time())
        self.assertTrue(await FarmService().plant_seed(TEST_USER_ID, 1, "수박"))
        ready_at = (await FarmService().get_farm(TEST_USER_ID))[0]["planted_at"]
        expected = reduced_grow_time(CROPS_DATA["수박"]["grow_time"], 5)
        self.assertAlmostEqual(ready_at - before, expected, delta=3)

    async def test_old_format_produce_still_sells(self):
        await self.inventory.update_inventory(TEST_USER_ID, "당근", 2)  # 예전 수확물
        result = await WarehouseService().sell_all(TEST_USER_ID)
        self.assertEqual(result["earned"], CROPS_DATA["당근"]["sell_price"] * 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
