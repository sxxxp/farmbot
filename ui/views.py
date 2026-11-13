import math
import time
from typing import List

import discord

from consistances import (
    CROPS_DATA,
    FERTILIZERS,
    GRADE_NAMES,
    HARVEST_CONFIG,
    PASSIVE_CONFIG,
    difficulty_name,
    grade_name,
)
from service.codex import CODEX_VARIANTS, CodexService
from service.craft import CraftService
from service.farm import FarmService, slots_for_level
from service.passive import PassiveService
from service.produce import (
    MAX_STARS,
    care_points,
    mutation_chance,
    produce_display_name,
    produce_price,
    rot_after_seconds,
    rot_at,
    star_odds,
)
from service.quest import QuestService, sum_rewards
from service.shop import ShopService, grade_odds
from service.user import UserService
from service.warehouse import WarehouseService
from ui.embed import (
    CODEX_TITLES,
    WAREHOUSE_PAGE_SIZE,
    codex_total_pages,
    embed_codex,
    embed_craft,
    embed_passives,
    embed_quests,
    embed_shop,
    embed_warehouse,
    fertilizer_effect_text,
    format_duration,
    format_reward,
    item_display_name,
    level_progress,
    user_header,
    won,
)
from ui.notice import NOTICE_DELETE_AFTER, send_notice
from utils.command_mention import command_mention
from utils.datatype import (
    CodexBoard,
    CraftBoard,
    FarmData,
    PassiveBoard,
    ProduceItem,
    QuestBoard,
    ShopBoard,
    ShopItem,
    WarehouseBoard,
)


def star_exp_note(stars: int) -> str:
    """★2 이상이면 ' (★3 보너스 x1.5)' 처럼 별 경험치 배율을 붙인다"""
    multipliers = HARVEST_CONFIG["star_exp_multipliers"]
    if not 1 <= stars <= len(multipliers) or multipliers[stars - 1] == 1:
        return ""
    return f" (★{stars} 보너스 x{multipliers[stars - 1]:g})"


def level_up_text(level: int, level_before: int | None = None) -> str:
    level_before = level - 1 if level_before is None else level_before
    text = (
        f"\n🎊 **레벨 업! 농장 레벨 {level}** — "
        f"{command_mention('패시브')}에서 새 포인트를 사용할 수 있어요."
    )
    new_slots = slots_for_level(level) - slots_for_level(level_before)
    if new_slots > 0:
        text += (
            f"\n🌱 **새 땅 {new_slots}칸이 열렸어요!** "
            f"(총 {slots_for_level(level)}칸 · {command_mention('농사')})"
        )
    return text


# 땅 상태: (아이콘, 이름). 순서는 요약 줄에 보이는 순서 (급한 것부터)
SLOT_STATES = {
    "ready": ("✅", "수확 가능"),
    "rotten": ("🥀", "썩음"),
    "growing": ("🌱", "자라는 중"),
    "empty": ("⬜", "빈 땅"),
}


def slot_state(slot: FarmData, now: float) -> str:
    crop = slot["crop_id"]
    if not crop:
        return "empty"
    ready_at = slot["planted_at"] or 0
    if crop in CROPS_DATA and now >= rot_at(crop, ready_at):
        return "rotten"
    return "ready" if now >= ready_at else "growing"


def slot_line(slot: FarmData, now: float, selected: bool) -> str:
    """농장 목록 한 줄. 앞쪽에 상태 아이콘을 두어 한눈에 훑을 수 있게 한다."""
    state = slot_state(slot, now)
    icon, label = SLOT_STATES[state]
    marker = "▶" if selected else "　"
    head = f"{marker}`{slot['slot_id']:>2}` {icon}"
    if state == "empty":
        return f"{head} {label}"

    crop = slot["crop_id"]
    ready_at = slot["planted_at"] or 0
    care = ("💧" if slot["is_watered"] else "") + ("🧪" if slot.get("fertilizer") else "")
    care = f" {care}" if care else ""
    if state == "growing":
        detail = f"<t:{ready_at}:R> 완료"
    elif state == "ready":
        detail = f"**수확 가능!** · 🥀 <t:{rot_at(crop, ready_at)}:R>"
    else:
        detail = "썩음 · 수확해서 땅을 비우세요"
    return f"{head} **{crop}**{care} · {detail}"


def slot_detail(slot: FarmData, now: float) -> str:
    """선택한 땅의 상세: 상태 / 관리 / 예상 품질 / 썩는 시간"""
    state = slot_state(slot, now)
    if state == "empty":
        return "빈 땅입니다. **씨앗 심기** 버튼으로 작물을 심어 보세요."

    crop = slot["crop_id"]
    fertilizer = slot.get("fertilizer")
    watered = bool(slot["is_watered"])
    ready_at = slot["planted_at"] or 0
    rots_at = rot_at(crop, ready_at)

    if state == "growing":
        status = f"🌱 자라는 중 · <t:{ready_at}:R> 수확 가능 (<t:{ready_at}:t>)"
    elif state == "ready":
        status = f"✅ **수확 가능!** · 🥀 <t:{rots_at}:R> 썩음"
    else:
        status = "🥀 **썩었습니다.** 수확하면 썩은 식물이 나옵니다."
    lines = [f"**상태** {status}"]

    lines.append(
        f"**관리** {'💧 물 줌' if watered else '☀️ 물 안 줌'} · "
        f"{'🧪 ' + item_display_name(fertilizer) if fertilizer else '비료 없음'} · "
        f"난이도 {difficulty_name(CROPS_DATA[crop]['difficulty'])}"
    )
    if state != "rotten":
        points = care_points(crop, watered, fertilizer, ready_at, max(ready_at, int(now)))
        high = sum(p for stars, p in star_odds(points).items() if stars >= 4)
        mutation = mutation_chance(watered, fertilizer) * 100
        lines.append(f"**예상** 제때 수확 시 ★4 이상 {high:.0f}% · 🧬 돌연변이 {mutation:.1f}%")
    if state == "growing":
        lines.append(
            f"-# 🥀 다 자란 뒤 {format_duration(rot_after_seconds(crop))} 안에 수확하지 않으면 썩습니다."
        )
    return "\n".join(lines)


class FarmView(discord.ui.View):
    PAGE_SIZE = 10

    def __init__(
        self,
        farm: List[FarmData],
        author: discord.User,
        page: int = 1,
        current: int | None = None,
    ):
        super().__init__(timeout=None)
        self.author = author
        # asyncpg Record 는 수정할 수 없으므로 dict 로 복사해서 보관
        self.farm = sorted((dict(row) for row in farm), key=lambda s: s["slot_id"])
        self.total_pages = max(1, math.ceil(len(self.farm) / self.PAGE_SIZE))
        self.page = min(max(page, 1), self.total_pages)
        start_idx = (self.page - 1) * self.PAGE_SIZE
        self.current_items = self.farm[start_idx : start_idx + self.PAGE_SIZE]

        # 선택된 땅이 현재 페이지에 없으면 페이지의 첫 번째 땅을 선택
        page_slot_ids = [item["slot_id"] for item in self.current_items]
        if current not in page_slot_ids:
            current = page_slot_ids[0] if page_slot_ids else None
        self.current = current

        self._build_components()
        add_game_nav(self, "farm")

    def get_slot(self, slot_id: int | None) -> FarmData | None:
        return next((s for s in self.farm if s["slot_id"] == slot_id), None)

    async def interaction_check(self, interaction: discord.Interaction):
        if interaction.user.id != self.author.id:
            await interaction.response.send_message(
                f"이 메뉴는 {self.author.display_name}님만 관리 가능합니다.",
                ephemeral=True,
            )
            return False
        return True

    def _build_components(self):
        if not self.current_items:
            return

        select_options = []
        for item in self.current_items:
            slot_id = item["slot_id"]
            crop_name = item["crop_id"] if item["crop_id"] else "없음"
            is_watered = "💧" if item["is_watered"] else "☀️"
            fertilized = " 🧪" if item.get("fertilizer") else ""
            select_options.append(
                discord.SelectOption(
                    label=f"{slot_id}번 땅 - {crop_name} {is_watered}{fertilized}",
                    value=str(slot_id),
                    description=f"클릭하여 {slot_id}번 땅 선택",
                    default=slot_id == self.current,
                )
            )

        select = discord.ui.Select(
            placeholder=f"🌱 농장 슬롯 선택 (페이지 {self.page}/{self.total_pages})",
            options=select_options,
            custom_id="farm_select",
        )
        select.callback = self.select_callback
        self.add_item(select)

        prev_button = discord.ui.Button(
            label="◀ 이전",
            disabled=self.page <= 1,
            style=discord.ButtonStyle.secondary,
            custom_id="prev_page",
            row=1,
        )
        prev_button.callback = self.prev_page_callback
        self.add_item(prev_button)

        next_button = discord.ui.Button(
            label="다음 ▶",
            disabled=self.page >= self.total_pages,
            style=discord.ButtonStyle.secondary,
            custom_id="next_page",
            row=1,
        )
        next_button.callback = self.next_page_callback
        self.add_item(next_button)

        # 액션 버튼은 현재 선택된 땅 기준으로 활성화
        selected = self.get_slot(self.current)
        planted = selected is not None and selected["crop_id"] is not None

        seed_button = discord.ui.Button(
            label="씨앗 심기",
            disabled=planted,
            style=discord.ButtonStyle.primary,
            row=2,
        )
        seed_button.callback = self.seed_button_callback

        water_button = discord.ui.Button(
            label="물주기",
            disabled=not planted or bool(selected["is_watered"]),
            style=discord.ButtonStyle.primary,
            row=2,
        )
        water_button.callback = self.water_button_callback

        harvest_button = discord.ui.Button(
            label="수확하기",
            disabled=not planted,
            style=discord.ButtonStyle.primary,
            row=2,
        )
        harvest_button.callback = self.harvest_button_callback

        # 비료: 자라는 중이고 이번 재배에 아직 비료를 안 줬을 때만
        growing = planted and (selected["planted_at"] or 0) > time.time()
        fertilizer_button = discord.ui.Button(
            label="비료 주기",
            disabled=not growing or bool(selected.get("fertilizer")),
            style=discord.ButtonStyle.primary,
            row=2,
        )
        fertilizer_button.callback = self.fertilizer_button_callback
        self.add_item(fertilizer_button)

        self.add_item(seed_button)
        self.add_item(water_button)
        self.add_item(harvest_button)

    async def show(self, interaction: discord.Interaction, view: "FarmView"):
        await interaction.response.edit_message(embed=view.create_embed(), view=view)

    async def refresh(self, interaction: discord.Interaction):
        """DB 에서 농장을 다시 읽어 메시지를 갱신"""
        farm = await FarmService().get_farm(self.author.id)
        await self.show(
            interaction, FarmView(farm, self.author, self.page, self.current)
        )

    async def water_button_callback(self, interaction: discord.Interaction):
        await FarmService().water_seed(self.author.id, self.current)
        await self.refresh(interaction)

    async def seed_button_callback(self, interaction: discord.Interaction):
        slot = self.current
        seeds = await FarmService().get_seeds(self.author.id)
        if not seeds:
            await send_notice(interaction, "보유한 씨앗이 없습니다. 🌱")
            return

        view = SeedSelectView(self, interaction.message, slot, seeds)
        await interaction.response.send_message(
            f"{slot}번 땅에 심을 작물을 선택하세요.", view=view, ephemeral=True
        )

    async def fertilizer_button_callback(self, interaction: discord.Interaction):
        slot = self.current
        fertilizers = await FarmService().get_fertilizers(self.author.id)
        if not fertilizers:
            await send_notice(
                interaction,
                f"보유한 비료가 없습니다. 🧪 {command_mention('상점')}에서 구매할 수 있어요.",
            )
            return

        view = FertilizerSelectView(self, interaction.message, slot, fertilizers)
        await interaction.response.send_message(
            f"{slot}번 땅에 줄 비료를 선택하세요.", view=view, ephemeral=True
        )

    async def harvest_button_callback(self, interaction: discord.Interaction):
        slot = self.current
        result = await FarmService().harvest_crop(self.author.id, slot)
        await self.refresh(interaction)
        if result and result.get("rotten"):
            msg = (
                f"{slot}번 땅의 {result['crop']}이(가) 너무 오래 방치되어 썩어 버렸습니다... 🥀\n"
                f"{produce_display_name(result['item_id'])}을(를) 얻었습니다. "
                f"(판매가 개당 {won(produce_price(result['item_id']))})"
            )
        elif result:
            name = produce_display_name(result["item_id"])
            msg = f"{slot}번 땅에서 {name}을(를) 수확했습니다! 🧺"
            if result.get("golden"):
                multiplier = HARVEST_CONFIG["golden"]["price_multiplier"]
                msg += f"\n✨ **황금 작물 발견!** 판매가 {multiplier:g}배!"
            elif result["mutant"]:
                multiplier = HARVEST_CONFIG["mutation"]["price_multiplier"]
                msg += f"\n🧬 **돌연변이 발생!** 판매가 {multiplier:g}배!"
            if result["stars"] == MAX_STARS:
                msg += "\n🌟 **최고 품질!**"
            if result.get("exp"):
                msg += f"\n✨ **경험치 +{result['exp']:,}xp**{star_exp_note(result['stars'])}"
            msg += f"\n(판매가 개당 {won(produce_price(result['item_id']))} · {command_mention('창고')}에서 판매)"
            if result.get("new_codex"):
                book = CODEX_TITLES[result.get("variant") or "normal"][1]
                msg += f"\n📖 **{book} 도감에 {result['crop']} 신규 등록!**"
            if result.get("level_up"):
                msg += level_up_text(result["level_up"])
        else:
            msg = f"{slot}번 땅의 작물은 아직 수확할 수 없습니다. ⏳"
        await send_notice(interaction, msg)

    async def select_callback(self, interaction: discord.Interaction):
        selected_slot = int(interaction.data["values"][0])
        await self.show(
            interaction, FarmView(self.farm, self.author, self.page, selected_slot)
        )

    async def prev_page_callback(self, interaction: discord.Interaction):
        await self.show(interaction, FarmView(self.farm, self.author, self.page - 1))

    async def next_page_callback(self, interaction: discord.Interaction):
        await self.show(interaction, FarmView(self.farm, self.author, self.page + 1))

    def create_embed(self, view: "FarmView | None" = None) -> discord.Embed:
        view = view or self
        embed = discord.Embed(title="🌾 농장", color=discord.Color.green())
        if not view.current_items:
            embed.description = "농장에 등록된 데이터가 없습니다."
            return embed

        now = time.time()
        # 전체 농장 요약 (0개인 상태는 생략)
        counts = {state: 0 for state in SLOT_STATES}
        for item in view.farm:
            counts[slot_state(item, now)] += 1
        summary = " · ".join(
            f"{SLOT_STATES[s][0]} {SLOT_STATES[s][1]} **{n}**" for s, n in counts.items() if n
        )
        lines = [
            slot_line(item, now, item["slot_id"] == view.current)
            for item in view.current_items
        ]
        embed.description = summary + "\n\n" + "\n".join(lines)

        selected = view.get_slot(view.current)
        if selected:
            crop = selected["crop_id"]
            if not crop or crop in CROPS_DATA:
                title = f"**{selected['slot_id']}번 땅" + (f" · {crop}**" if crop else "**")
                embed.add_field(
                    name="📋 상세 정보",
                    value=f"{title}\n{slot_detail(selected, now)}",
                    inline=False,
                )

        embed.set_footer(
            text=f"페이지 {view.page}/{view.total_pages} · 💧 물 줌 · 🧪 비료 줌"
        )
        return embed


class SeedSelectView(discord.ui.View):
    """씨앗 심기 버튼을 눌렀을 때 심을 작물을 고르는 선택 메뉴.
    선택 메뉴는 25개까지라 씨앗 종류가 많으면 페이지로 나눈다."""

    PAGE_SIZE = 25

    def __init__(
        self,
        farm_view: FarmView,
        farm_message: discord.Message,
        slot: int,
        seeds: List[tuple[str, int]],
        page: int = 1,
    ):
        super().__init__(timeout=60)
        self.farm_view = farm_view
        self.farm_message = farm_message
        self.slot = slot
        # 등급 낮은 순 → 이름 순
        self.seeds = sorted(
            seeds, key=lambda s: (CROPS_DATA[s[0]]["buy_grade"], s[0])
        )
        self.total_pages = max(1, math.ceil(len(self.seeds) / self.PAGE_SIZE))
        self.page = min(max(page, 1), self.total_pages)

        start = (self.page - 1) * self.PAGE_SIZE
        options = []
        for crop_name, amount in self.seeds[start : start + self.PAGE_SIZE]:
            crop = CROPS_DATA[crop_name]
            options.append(
                discord.SelectOption(
                    label=crop_name,
                    value=crop_name,
                    description=(
                        f"{grade_name(crop['buy_grade'])} · "
                        f"성장 {format_duration(crop['grow_time'])} · 보유 {amount}개"
                    ),
                )
            )

        placeholder = "🌱 심을 작물 선택"
        if self.total_pages > 1:
            placeholder += f" ({self.page}/{self.total_pages})"
        select = discord.ui.Select(placeholder=placeholder, options=options)
        select.callback = self.select_callback
        self.add_item(select)

        if self.total_pages > 1:
            for label, delta, disabled in (
                ("◀ 이전", -1, self.page <= 1),
                ("다음 ▶", 1, self.page >= self.total_pages),
            ):
                button = discord.ui.Button(
                    label=label,
                    style=discord.ButtonStyle.secondary,
                    disabled=disabled,
                    row=1,
                )
                button.callback = self._make_page_callback(self.page + delta)
                self.add_item(button)

    def _make_page_callback(self, page: int):
        async def callback(interaction: discord.Interaction):
            view = SeedSelectView(
                self.farm_view, self.farm_message, self.slot, self.seeds, page
            )
            await interaction.response.edit_message(view=view)

        return callback

    async def select_callback(self, interaction: discord.Interaction):
        crop_name = interaction.data["values"][0]
        author = self.farm_view.author

        planted = await FarmService().plant_seed(author.id, self.slot, crop_name)
        if not planted:
            await interaction.response.edit_message(
                content=f"{self.slot}번 땅에 {crop_name}을(를) 심지 못했습니다. "
                "(땅이 비어있지 않거나 씨앗이 부족합니다)",
                view=None,
                delete_after=NOTICE_DELETE_AFTER,
            )
            return

        await interaction.response.edit_message(
            content=f"{self.slot}번 땅에 {crop_name}을(를) 심었습니다! 🌱",
            view=None,
            delete_after=NOTICE_DELETE_AFTER,
        )

        farm = await FarmService().get_farm(author.id)
        new_view = FarmView(farm, author, self.farm_view.page, self.slot)
        await self.farm_message.edit(embed=new_view.create_embed(), view=new_view)


class FertilizerSelectView(discord.ui.View):
    """비료 주기 버튼을 눌렀을 때 사용할 비료를 고르는 선택 메뉴"""

    def __init__(
        self,
        farm_view: FarmView,
        farm_message: discord.Message,
        slot: int,
        fertilizers: List[tuple[str, int]],
    ):
        super().__init__(timeout=60)
        self.farm_view = farm_view
        self.farm_message = farm_message
        self.slot = slot

        options = []
        for item_id, amount in fertilizers[:25]:
            data = FERTILIZERS[item_id]
            options.append(
                discord.SelectOption(
                    label=data["name"],
                    value=item_id,
                    description=f"{fertilizer_effect_text(item_id)} · 보유 {amount}개",
                )
            )
        select = discord.ui.Select(placeholder="🧪 사용할 비료 선택", options=options)
        select.callback = self.select_callback
        self.add_item(select)

    async def select_callback(self, interaction: discord.Interaction):
        item_id = interaction.data["values"][0]
        author = self.farm_view.author
        name = item_display_name(item_id)

        ready_at = await FarmService().apply_fertilizer(author.id, self.slot, item_id)
        if ready_at is None:
            await interaction.response.edit_message(
                content=f"{self.slot}번 땅에 {name}를 줄 수 없습니다. "
                "(이미 비료를 줬거나 다 자랐습니다)",
                view=None,
                delete_after=NOTICE_DELETE_AFTER,
            )
            return

        await interaction.response.edit_message(
            content=f"🧪 {self.slot}번 땅에 {name}를 줬습니다! "
            f"{fertilizer_effect_text(item_id)} → 수확 가능 <t:{ready_at}:R>",
            view=None,
            delete_after=NOTICE_DELETE_AFTER,
        )

        farm = await FarmService().get_farm(author.id)
        new_view = FarmView(farm, author, self.farm_view.page, self.slot)
        await self.farm_message.edit(embed=new_view.create_embed(), view=new_view)


class QuestView(discord.ui.View):
    """/퀘스트 화면: 메인/일일/주간 탭 버튼 + 보상 받기 버튼"""

    TABS = (("main", "메인"), ("daily", "일일"), ("weekly", "주간"))

    def __init__(self, author: discord.User, board: QuestBoard):
        super().__init__(timeout=None)
        self.author = author
        self.board = board
        self.quest_type = board["quest_type"]
        self._build_components()
        add_game_nav(self, "quest")

    @property
    def claimable(self) -> int:
        return sum(
            1
            for s in self.board["quests"]
            if s["completed"] and not s["claimed"] and not s["locked"]
        )

    def create_embed(self) -> discord.Embed:
        return embed_quests(self.board, self.author.display_name)

    async def interaction_check(self, interaction: discord.Interaction):
        if interaction.user.id != self.author.id:
            await interaction.response.send_message(
                f"이 메뉴는 {self.author.display_name}님만 사용할 수 있습니다.",
                ephemeral=True,
            )
            return False
        return True

    def _build_components(self):
        for quest_type, label in self.TABS:
            selected = quest_type == self.quest_type
            button = discord.ui.Button(
                label=label,
                style=(
                    discord.ButtonStyle.primary
                    if selected
                    else discord.ButtonStyle.secondary
                ),
                disabled=selected,
                row=0,
            )
            button.callback = self._make_tab_callback(quest_type)
            self.add_item(button)

        claim_button = discord.ui.Button(
            label=f"🎁 보상 받기 ({self.claimable})",
            style=discord.ButtonStyle.success,
            disabled=self.claimable == 0,
            row=1,
        )
        claim_button.callback = self.claim_callback
        self.add_item(claim_button)

    def _make_tab_callback(self, quest_type: str):
        async def callback(interaction: discord.Interaction):
            await self.show(interaction, quest_type)

        return callback

    async def show(self, interaction: discord.Interaction, quest_type: str) -> QuestBoard:
        board = await QuestService().get_quests(self.author.id, quest_type)
        view = QuestView(self.author, board)
        await interaction.response.edit_message(embed=view.create_embed(), view=view)
        return board

    async def claim_callback(self, interaction: discord.Interaction):
        level_before = self.board["level"]
        claimed = await QuestService().claim_rewards(self.author.id, self.quest_type)
        board = await self.show(interaction, self.quest_type)
        if claimed:
            titles = ", ".join(q["title"] for q in claimed)
            msg = (
                f"🎉 **{titles}** 보상을 받았습니다!\n"
                f"{format_reward(sum_rewards(claimed))}"
            )
            if board["level"] > level_before:
                msg += level_up_text(board["level"], level_before)
        else:
            msg = "받을 수 있는 보상이 없습니다."
        await send_notice(interaction, msg)


class ShopView(discord.ui.View):
    """/상점 화면: 품목 선택 메뉴 + 구매 버튼 (1개 / 5개 / 최대)"""

    def __init__(
        self, author: discord.User, board: ShopBoard, selected: int | None = None
    ):
        super().__init__(timeout=None)
        self.author = author
        self.board = board

        slots = [item["slot"] for item in board["items"]]
        if selected not in slots:
            # 기본 선택: 품절이 아닌 첫 품목
            in_stock = [i["slot"] for i in board["items"] if self.remaining(i) > 0]
            selected = (in_stock or slots or [None])[0]
        self.selected = selected

        self._build_components()
        add_game_nav(self, "shop")

    @staticmethod
    def remaining(item: ShopItem) -> int:
        return item["stock"] - item["bought"]

    @property
    def selected_item(self) -> ShopItem | None:
        return next((i for i in self.board["items"] if i["slot"] == self.selected), None)

    def max_amount(self) -> int:
        """선택한 품목을 지금 살 수 있는 최대 개수 (재고와 골드 기준)"""
        item = self.selected_item
        if item is None or item["price"] <= 0:
            return 0
        return max(0, min(self.remaining(item), self.board["gold"] // item["price"]))

    def create_embed(self) -> discord.Embed:
        odds = {
            category: grade_odds(category, self.board["level"])
            for category in ("seed", "fertilizer")
        }
        return embed_shop(self.board, self.author.display_name, odds, self.selected)

    async def interaction_check(self, interaction: discord.Interaction):
        if interaction.user.id != self.author.id:
            await interaction.response.send_message(
                f"이 상점은 {self.author.display_name}님 전용입니다. {command_mention('상점')}으로 열어 보세요.",
                ephemeral=True,
            )
            return False
        return True

    def _build_components(self):
        if not self.board["items"]:
            return

        options = []
        for item in self.board["items"][:25]:
            remaining = self.remaining(item)
            grade = grade_name(item["grade"])
            options.append(
                discord.SelectOption(
                    label=f"{item_display_name(item['item_id'])} ({grade})",
                    value=str(item["slot"]),
                    description=(
                        f"{won(item['price'])} · 재고 {remaining}/{item['stock']}"
                        if remaining > 0
                        else "품절"
                    ),
                    emoji="🌱" if item["category"] == "seed" else "🧪",
                    default=item["slot"] == self.selected,
                )
            )
        select = discord.ui.Select(placeholder="🛒 구매할 품목 선택", options=options)
        select.callback = self.select_callback
        self.add_item(select)

        max_amount = self.max_amount()
        for label, amount in (("1개 구매", 1), ("5개 구매", 5)):
            button = discord.ui.Button(
                label=label,
                style=discord.ButtonStyle.success,
                disabled=max_amount < amount,
                row=1,
            )
            button.callback = self._make_buy_callback(amount)
            self.add_item(button)

        max_button = discord.ui.Button(
            label=f"최대 구매 ({max_amount}개)",
            style=discord.ButtonStyle.primary,
            disabled=max_amount < 1,
            row=1,
        )
        max_button.callback = self._make_buy_callback(None)
        self.add_item(max_button)

    async def select_callback(self, interaction: discord.Interaction):
        selected = int(interaction.data["values"][0])
        view = ShopView(self.author, self.board, selected)
        await interaction.response.edit_message(embed=view.create_embed(), view=view)

    def _make_buy_callback(self, amount: int | None):
        async def callback(interaction: discord.Interaction):
            await self.buy(interaction, amount if amount is not None else self.max_amount())

        return callback

    async def buy(self, interaction: discord.Interaction, amount: int):
        item = self.selected_item
        result = await ShopService().buy(self.author.id, self.selected, amount)

        board = await ShopService().get_shop(self.author.id)
        view = ShopView(self.author, board, self.selected)
        await interaction.response.edit_message(embed=view.create_embed(), view=view)

        name = item_display_name(item["item_id"]) if item else "품목"
        if result["ok"]:
            msg = (
                f"🛒 {name} x{result['amount']} 구매 완료! "
                f"(-{won(result['cost'])}, 남은 골드 {won(board['gold'])})"
            )
        elif result["reason"] == "no_gold":
            msg = f"골드가 부족합니다. (필요: {won(result['cost'])}, 보유: {won(board['gold'])})"
        elif result["reason"] == "sold_out":
            msg = f"{name}의 재고가 부족하거나 상점이 새로 입고되었습니다."
        else:
            msg = "구매할 수 없습니다."
        await send_notice(interaction, msg)


class WarehouseView(discord.ui.View):
    """/창고 화면: 수확물 선택 + 판매 버튼 (1개 / 선택 품목 전부 / 수확물 모두)"""

    def __init__(
        self,
        author: discord.User,
        board: WarehouseBoard,
        page: int = 1,
        selected: str | None = None,
    ):
        super().__init__(timeout=None)
        self.author = author
        self.board = board
        produce = board["produce"]
        self.total_pages = max(1, math.ceil(len(produce) / WAREHOUSE_PAGE_SIZE))
        self.page = min(max(page, 1), self.total_pages)
        start = (self.page - 1) * WAREHOUSE_PAGE_SIZE
        self.page_items = produce[start : start + WAREHOUSE_PAGE_SIZE]

        page_ids = [p["item_id"] for p in self.page_items]
        if selected not in page_ids:
            selected = page_ids[0] if page_ids else None
        self.selected = selected

        self._build_components()
        add_game_nav(self, "warehouse")

    @property
    def selected_item(self) -> ProduceItem | None:
        return next((p for p in self.page_items if p["item_id"] == self.selected), None)

    def create_embed(self) -> discord.Embed:
        return embed_warehouse(
            self.board, self.author.display_name, self.page, self.selected
        )

    async def interaction_check(self, interaction: discord.Interaction):
        if interaction.user.id != self.author.id:
            await interaction.response.send_message(
                f"이 창고는 {self.author.display_name}님 전용입니다. {command_mention('창고')}로 열어 보세요.",
                ephemeral=True,
            )
            return False
        return True

    def _build_components(self):
        if not self.page_items:
            return

        options = [
            discord.SelectOption(
                label=produce_display_name(p["item_id"]),
                value=p["item_id"],
                description=f"보유 {p['amount']}개 · 개당 {won(p['price'])}",
                default=p["item_id"] == self.selected,
            )
            for p in self.page_items
        ]
        select = discord.ui.Select(placeholder="🧺 판매할 작물 선택", options=options)
        select.callback = self.select_callback
        self.add_item(select)

        item = self.selected_item
        amount = item["amount"] if item else 0
        sell_one = discord.ui.Button(
            label="1개 판매", style=discord.ButtonStyle.success, disabled=amount < 1, row=1
        )
        sell_one.callback = self._make_sell_callback(1)
        self.add_item(sell_one)

        sell_stack = discord.ui.Button(
            label=f"전부 판매 ({amount}개)",
            style=discord.ButtonStyle.success,
            disabled=amount < 1,
            row=1,
        )
        sell_stack.callback = self._make_sell_callback(None)
        self.add_item(sell_stack)

        total = sum(p["price"] * p["amount"] for p in self.board["produce"])
        sell_all = discord.ui.Button(
            label=f"수확물 모두 판매 ({won(total)})",
            style=discord.ButtonStyle.danger,
            row=1,
        )
        sell_all.callback = self.sell_all_callback
        self.add_item(sell_all)

        if self.total_pages > 1:
            for label, delta, disabled in (
                ("◀ 이전", -1, self.page <= 1),
                ("다음 ▶", 1, self.page >= self.total_pages),
            ):
                button = discord.ui.Button(
                    label=label,
                    style=discord.ButtonStyle.secondary,
                    disabled=disabled,
                    row=2,
                )
                button.callback = self._make_page_callback(self.page + delta)
                self.add_item(button)

    async def show(self, interaction: discord.Interaction, view: "WarehouseView"):
        await interaction.response.edit_message(embed=view.create_embed(), view=view)

    async def refresh(self, interaction: discord.Interaction):
        board = await WarehouseService().get_warehouse(self.author.id)
        await self.show(interaction, WarehouseView(self.author, board, self.page, self.selected))

    async def select_callback(self, interaction: discord.Interaction):
        selected = interaction.data["values"][0]
        await self.show(interaction, WarehouseView(self.author, self.board, self.page, selected))

    def _make_page_callback(self, page: int):
        async def callback(interaction: discord.Interaction):
            await self.show(interaction, WarehouseView(self.author, self.board, page))

        return callback

    def _make_sell_callback(self, amount: int | None):
        async def callback(interaction: discord.Interaction):
            item = self.selected_item
            count = amount if amount is not None else (item["amount"] if item else 0)
            await self.sell(interaction, count)

        return callback

    async def sell(self, interaction: discord.Interaction, amount: int):
        item = self.selected_item
        result = await WarehouseService().sell(self.author.id, self.selected, amount)
        await self.refresh(interaction)
        if result["ok"]:
            msg = (
                f"💰 {produce_display_name(item['item_id'])} x{result['sold']} 판매! "
                f"+{won(result['earned'])}"
            )
        else:
            msg = "판매할 수 없습니다. (보유 수량이 부족합니다)"
        await send_notice(interaction, msg)

    async def sell_all_callback(self, interaction: discord.Interaction):
        result = await WarehouseService().sell_all(self.author.id)
        await self.refresh(interaction)
        if result["ok"]:
            msg = f"💰 수확물 {result['sold']}개를 모두 판매했습니다! +{won(result['earned'])}"
        else:
            msg = "판매할 수확물이 없습니다."
        await send_notice(interaction, msg)


class PassiveView(discord.ui.View):
    """/패시브 화면: 노드 선택 + 포인트 투자 + 초기화(확인 단계 있음)"""

    INVEST_ERRORS = {
        "no_points": "남은 포인트가 없습니다. 레벨을 올리면 포인트를 얻어요.",
        "locked": "선행 패시브를 먼저 찍어야 합니다. 🔒",
        "maxed": "이미 최대 랭크입니다.",
        "unknown": "알 수 없는 패시브입니다.",
    }

    def __init__(
        self,
        author: discord.User,
        board: PassiveBoard,
        selected: str | None = None,
        confirm_reset: bool = False,
    ):
        super().__init__(timeout=None)
        self.author = author
        self.board = board
        ids = [s["node"]["id"] for s in board["nodes"]]
        if selected not in ids:
            # 기본 선택: 지금 찍을 수 있는 첫 노드
            investable = [s["node"]["id"] for s in board["nodes"]
                          if s["unlocked"] and not s["maxed"]]
            selected = (investable or ids or [None])[0]
        self.selected = selected
        self.confirm_reset = confirm_reset
        self._build_components()
        add_game_nav(self, "passive")

    @property
    def selected_status(self):
        return next((s for s in self.board["nodes"] if s["node"]["id"] == self.selected), None)

    def create_embed(self) -> discord.Embed:
        return embed_passives(self.board, self.author.display_name, self.selected)

    async def interaction_check(self, interaction: discord.Interaction):
        if interaction.user.id != self.author.id:
            await interaction.response.send_message(
                f"이 패시브 트리는 {self.author.display_name}님 전용입니다.", ephemeral=True
            )
            return False
        return True

    def _build_components(self):
        branches = {b["id"]: b for b in PASSIVE_CONFIG["branches"]}
        options = []
        for status in self.board["nodes"][:25]:
            node = status["node"]
            state = "🔒 " if not status["unlocked"] else ""
            options.append(
                discord.SelectOption(
                    label=f"{state}{node['name']} [{status['rank']}/{node['max_rank']}]",
                    value=node["id"],
                    description=node["description"][:100],
                    emoji=branches.get(node["branch"], {}).get("emoji"),
                    default=node["id"] == self.selected,
                )
            )
        if options:
            select = discord.ui.Select(placeholder="🌳 패시브 선택", options=options)
            select.callback = self.select_callback
            self.add_item(select)

        if self.confirm_reset:
            confirm = discord.ui.Button(
                label=f"정말 초기화 ({won(self.board['reset_cost'])})",
                style=discord.ButtonStyle.danger,
                row=1,
            )
            confirm.callback = self.reset_callback
            cancel = discord.ui.Button(
                label="취소", style=discord.ButtonStyle.secondary, row=1
            )
            cancel.callback = self.cancel_reset_callback
            self.add_item(confirm)
            self.add_item(cancel)
            return

        status = self.selected_status
        can_invest = (
            status is not None
            and self.board["available_points"] > 0
            and status["unlocked"]
            and not status["maxed"]
        )
        invest = discord.ui.Button(
            label=f"포인트 투자 (남은 {self.board['available_points']})",
            style=discord.ButtonStyle.success,
            disabled=not can_invest,
            row=1,
        )
        invest.callback = self.invest_callback
        self.add_item(invest)

        reset = discord.ui.Button(
            label="초기화",
            style=discord.ButtonStyle.danger,
            disabled=self.board["spent_points"] == 0,
            row=1,
        )
        reset.callback = self.ask_reset_callback
        self.add_item(reset)

    async def show(self, interaction: discord.Interaction, view: "PassiveView"):
        await interaction.response.edit_message(embed=view.create_embed(), view=view)

    async def refresh(self, interaction: discord.Interaction):
        board = await PassiveService().get_board(self.author.id)
        await self.show(interaction, PassiveView(self.author, board, self.selected))

    async def select_callback(self, interaction: discord.Interaction):
        selected = interaction.data["values"][0]
        await self.show(interaction, PassiveView(self.author, self.board, selected))

    async def invest_callback(self, interaction: discord.Interaction):
        status = self.selected_status
        error = await PassiveService().invest(self.author.id, self.selected)
        await self.refresh(interaction)
        if error:
            await send_notice(interaction, self.INVEST_ERRORS.get(error, "투자할 수 없습니다."))
        else:
            node = status["node"]
            await send_notice(
                interaction,
                f"🌳 **{node['name']}** 랭크 {status['rank'] + 1}/{node['max_rank']}! "
                f"({node['description']})",
            )

    async def ask_reset_callback(self, interaction: discord.Interaction):
        await self.show(
            interaction, PassiveView(self.author, self.board, self.selected, True)
        )

    async def cancel_reset_callback(self, interaction: discord.Interaction):
        await self.show(interaction, PassiveView(self.author, self.board, self.selected))

    async def reset_callback(self, interaction: discord.Interaction):
        ok, cost = await PassiveService().reset(self.author.id)
        await self.refresh(interaction)
        if ok:
            msg = f"🔄 패시브를 초기화했습니다. (-{won(cost)}) 포인트를 다시 찍어 보세요."
        elif cost:
            msg = f"골드가 부족합니다. (초기화 비용 {won(cost)})"
        else:
            msg = "초기화할 패시브가 없습니다."
        await send_notice(interaction, msg)


class CraftView(discord.ui.View):
    """/제작 화면: 레시피 선택 + 1회 / 최대 제작"""

    CRAFT_ERRORS = {
        "locked": "농장 레벨이 부족합니다. 🔒",
        "no_materials": "재료가 부족합니다.",
        "no_gold": "골드가 부족합니다.",
        "invalid": "제작할 수 없습니다.",
        "unknown": "알 수 없는 레시피입니다.",
    }

    def __init__(self, author: discord.User, board: CraftBoard, selected: str | None = None):
        super().__init__(timeout=None)
        self.author = author
        self.board = board
        ids = [s["recipe"]["id"] for s in board["recipes"]]
        if selected not in ids:
            craftable = [s["recipe"]["id"] for s in board["recipes"] if s["max_crafts"]]
            selected = (craftable or ids or [None])[0]
        self.selected = selected
        self._build_components()
        add_game_nav(self, "craft")

    @property
    def selected_status(self):
        return next(
            (s for s in self.board["recipes"] if s["recipe"]["id"] == self.selected), None
        )

    def create_embed(self) -> discord.Embed:
        return embed_craft(self.board, self.author.display_name, self.selected)

    async def interaction_check(self, interaction: discord.Interaction):
        if interaction.user.id != self.author.id:
            await interaction.response.send_message(
                f"이 제작대는 {self.author.display_name}님 전용입니다. {command_mention('제작')}으로 열어 보세요.",
                ephemeral=True,
            )
            return False
        return True

    def _build_components(self):
        if not self.board["recipes"]:
            return
        options = []
        for status in self.board["recipes"][:25]:
            recipe = status["recipe"]
            if status["locked"]:
                desc = f"🔒 농장 레벨 {recipe['min_level']} 필요"
            elif status["max_crafts"]:
                desc = f"제작 가능 {status['max_crafts']}회"
            else:
                desc = "재료 부족"
            options.append(
                discord.SelectOption(
                    label=recipe["name"],
                    value=recipe["id"],
                    description=desc,
                    default=recipe["id"] == self.selected,
                )
            )
        select = discord.ui.Select(placeholder="🔨 제작할 레시피 선택", options=options)
        select.callback = self.select_callback
        self.add_item(select)

        status = self.selected_status
        max_crafts = status["max_crafts"] if status else 0
        once = discord.ui.Button(
            label="1회 제작", style=discord.ButtonStyle.success,
            disabled=max_crafts < 1, row=1,
        )
        once.callback = self._make_craft_callback(1)
        self.add_item(once)
        many = discord.ui.Button(
            label=f"최대 제작 ({max_crafts}회)", style=discord.ButtonStyle.primary,
            disabled=max_crafts < 2, row=1,
        )
        many.callback = self._make_craft_callback(None)
        self.add_item(many)

    async def show(self, interaction: discord.Interaction, view: "CraftView"):
        await interaction.response.edit_message(embed=view.create_embed(), view=view)

    async def select_callback(self, interaction: discord.Interaction):
        selected = interaction.data["values"][0]
        await self.show(interaction, CraftView(self.author, self.board, selected))

    def _make_craft_callback(self, times: int | None):
        async def callback(interaction: discord.Interaction):
            status = self.selected_status
            count = times if times is not None else (status["max_crafts"] if status else 0)
            await self.craft(interaction, count)

        return callback

    async def craft(self, interaction: discord.Interaction, times: int):
        result = await CraftService().craft(self.author.id, self.selected, times)
        board = await CraftService().get_board(self.author.id)
        await self.show(interaction, CraftView(self.author, board, self.selected))
        if result["ok"]:
            msg = f"🔨 {item_display_name(result['item'])} x{result['count']} 제작 완료!"
            if result["gold"]:
                msg += f" (-{won(result['gold'])})"
        else:
            msg = self.CRAFT_ERRORS.get(result["reason"], "제작할 수 없습니다.")
        await send_notice(interaction, msg)


class CodexView(discord.ui.View):
    """/도감 화면: 일반 / 황금 / 돌연변이 탭 + 페이지"""

    def __init__(self, author: discord.User, board: CodexBoard, page: int = 1):
        super().__init__(timeout=None)
        self.author = author
        self.board = board
        self.total_pages = codex_total_pages(board)
        self.page = min(max(page, 1), self.total_pages)
        self._build_components()
        add_game_nav(self, "codex")

    def create_embed(self) -> discord.Embed:
        return embed_codex(self.board, self.author.display_name, self.page)

    async def interaction_check(self, interaction: discord.Interaction):
        if interaction.user.id != self.author.id:
            await interaction.response.send_message(
                f"이 도감은 {self.author.display_name}님 전용입니다. "
                f"{command_mention('도감')}으로 열어 보세요.",
                ephemeral=True,
            )
            return False
        return True

    def _build_components(self):
        for variant in CODEX_VARIANTS:
            found, total = self.board["progress"][variant]
            selected = variant == self.board["variant"]
            button = discord.ui.Button(
                label=f"{CODEX_TITLES[variant][1]} ({found}/{total})",
                style=discord.ButtonStyle.primary if selected else discord.ButtonStyle.secondary,
                disabled=selected,
                row=0,
            )
            button.callback = self._make_tab_callback(variant)
            self.add_item(button)

        for label, delta, disabled in (
            ("◀ 이전", -1, self.page <= 1),
            ("다음 ▶", 1, self.page >= self.total_pages),
        ):
            button = discord.ui.Button(
                label=label, style=discord.ButtonStyle.secondary, disabled=disabled, row=1
            )
            button.callback = self._make_page_callback(self.page + delta)
            self.add_item(button)

    async def show(self, interaction: discord.Interaction, view: "CodexView"):
        await interaction.response.edit_message(embed=view.create_embed(), view=view)

    def _make_tab_callback(self, variant: str):
        async def callback(interaction: discord.Interaction):
            board = await CodexService().get_codex(self.author.id, variant)
            await self.show(interaction, CodexView(self.author, board))

        return callback

    def _make_page_callback(self, page: int):
        async def callback(interaction: discord.Interaction):
            await self.show(interaction, CodexView(self.author, self.board, page))

        return callback


# ------------------------------------------------------------------ 게임창 (화면 이동)

# (key, 이름, 이모지, 설명). 순서대로 선택 메뉴에 표시된다.
GAME_SCREENS: list[tuple[str, str, str, str]] = [
    ("home", "게임창", "🏠", "내 농장 요약과 메뉴"),
    ("farm", "농장", "🌾", "씨앗 심기 · 물주기 · 비료 · 수확"),
    ("shop", "상점", "🛒", "씨앗과 비료 구매"),
    ("craft", "제작소", "🔨", "수확물로 비료 · 씨앗 제작"),
    ("warehouse", "창고", "📦", "보유 아이템 확인 · 수확물 판매"),
    ("codex", "도감", "📖", "수확한 작물 도감과 보상"),
    ("passive", "패시브", "🌳", "레벨업 포인트로 패시브 찍기"),
    ("quest", "퀘스트", "📜", "메인 · 일일 · 주간 퀘스트"),
]
GAME_NAV_ROW = 4  # 각 화면은 0~2줄만 쓰므로 맨 아랫줄에 둔다


class GameNavSelect(discord.ui.Select):
    """모든 게임 화면 맨 아래에 붙는 화면 이동 메뉴.
    권한 체크는 이 메뉴가 붙은 View 의 interaction_check 가 한다."""

    def __init__(self, author: discord.User, current: str):
        self.author = author
        options = [
            discord.SelectOption(
                label=name, value=key, emoji=emoji, description=desc, default=key == current
            )
            for key, name, emoji, desc in GAME_SCREENS
        ]
        super().__init__(placeholder="🧭 이동할 화면 선택", options=options, row=GAME_NAV_ROW)

    async def callback(self, interaction: discord.Interaction):
        screen = self.values[0]
        embed, view = await build_game_screen(screen, self.author)
        await interaction.response.edit_message(embed=embed, view=view)


def add_game_nav(view: discord.ui.View, current: str) -> None:
    view.add_item(GameNavSelect(view.author, current))


class GameHomeView(discord.ui.View):
    """/게임창 첫 화면: 요약 embed + 화면 이동 메뉴"""

    def __init__(self, author: discord.User, user: dict):
        super().__init__(timeout=None)
        self.author = author
        self.user = user
        add_game_nav(self, "home")

    async def interaction_check(self, interaction: discord.Interaction):
        if interaction.user.id != self.author.id:
            await interaction.response.send_message(
                f"이 게임창은 {self.author.display_name}님 전용입니다. "
                f"{command_mention('게임창')}으로 열어 보세요.",
                ephemeral=True,
            )
            return False
        return True

    def create_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title=f"🏠 {self.author.display_name}님의 농장",
            description=(
                f"{user_header(self.author.display_name, self.user['level'], self.user['gold'])}\n"
                f"{level_progress(self.user['farm_exp'], self.user['level'])}"
            ),
            color=discord.Color.green(),
        )
        embed.add_field(
            name="메뉴",
            value="\n".join(
                f"{emoji} **{name}** — {desc}"
                for key, name, emoji, desc in GAME_SCREENS
                if key != "home"
            ),
            inline=False,
        )
        embed.set_footer(text="아래 메뉴에서 이동할 화면을 고르세요. 어느 화면에서든 다시 이동할 수 있어요.")
        return embed


async def build_game_screen(
    screen: str, author: discord.User
) -> tuple[discord.Embed, discord.ui.View]:
    """화면 key → (embed, view). 각 화면의 View 에는 이동 메뉴가 붙어 있다."""
    user_id = author.id
    if screen == "farm":
        view = FarmView(await FarmService().get_farm(user_id), author)
    elif screen == "shop":
        view = ShopView(author, await ShopService().get_shop(user_id))
    elif screen == "craft":
        view = CraftView(author, await CraftService().get_board(user_id))
    elif screen == "warehouse":
        view = WarehouseView(author, await WarehouseService().get_warehouse(user_id))
    elif screen == "codex":
        view = CodexView(author, await CodexService().get_codex(user_id))
    elif screen == "passive":
        view = PassiveView(author, await PassiveService().get_board(user_id))
    elif screen == "quest":
        view = QuestView(author, await QuestService().get_quests(user_id, "main"))
    else:
        view = GameHomeView(author, await UserService().get_user(user_id))
    return view.create_embed(), view
