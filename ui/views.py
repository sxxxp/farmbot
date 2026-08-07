from typing import List
import discord
from utils.datatype import FarmData
from service.farm import FarmService

import math
from typing import List
import discord


class FarmView(discord.ui.View):
    def __init__(self, farm: List[FarmData], author: discord.User, page: int = 1):
        super().__init__(timeout=None)
        self.author = author
        self.farm = farm
        self.page = page
        self.total_pages = math.ceil(len(farm) / 10) if farm else 1
        self.current = 1
        start_idx = (self.page - 1) * 10
        end_idx = start_idx + 10
        self.current_items = self.farm[start_idx:end_idx]

        self._build_components()

    async def interaction_check(self, interaction: discord.Interaction):
        if interaction.user.id != self.author.id:
            await interaction.response.send_message(
                f"이 메뉴는 {self.author.display_name}님만 관리 가능합니다.",
                ephemeral=True,
            )
            return False
        return True

    def _build_components(self):
        if self.current_items:
            select_options = []
            for item in self.current_items:
                slot_id = item["slot_id"]
                crop_name = item["crop_id"] if item["crop_id"] else "없음"
                is_watered = "💧" if item["is_watered"] else "☀️"
                select_options.append(
                    discord.SelectOption(
                        label=f"{slot_id}번 땅 - {crop_name} {is_watered}",
                        value=str(slot_id),
                        description=f"클릭하여 {slot_id}번 땅 상세 보기",
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
                disabled=not self.page > 1,
                style=discord.ButtonStyle.secondary,
                custom_id="prev_page",
                row=1,
            )
            prev_button.callback = self.prev_page_callback
            self.add_item(prev_button)

            next_button = discord.ui.Button(
                label="다음 ▶",
                disabled=not self.page < self.total_pages,
                style=discord.ButtonStyle.secondary,
                custom_id="next_page",
                row=1,
            )
            next_button.callback = self.next_page_callback
            self.add_item(next_button)

            seed_button = discord.ui.Button(
                label="씨앗 심기",
                disabled=self.farm[self.current - 1]["crop_id"] is not None,
                style=discord.ButtonStyle.primary,
                row=2,
            )
            seed_button.callback = self.seed_button_callback

            water_button = discord.ui.Button(
                label="물주기",
                disabled=self.farm[self.current - 1]["is_watered"] is not None,
                style=discord.ButtonStyle.primary,
                row=2,
            )
            water_button.callback = self.water_button_callback
            harvest_button = discord.ui.Button(
                label="수확하기",
                style=discord.ButtonStyle.primary,
                row=2,
            )
            harvest_button.callback = self.harvest_button_callback

            self.add_item(seed_button)
            self.add_item(water_button)
            self.add_item(harvest_button)

    async def rewrite_view(self, interaction: discord.Interaction):
        new_view = FarmView(self.farm, self.author, page=self.page)
        embed = self.create_embed(new_view)
        await interaction.response.edit_message(embed=embed, view=new_view)

    async def water_button_callback(self, interaction: discord.Interaction):
        slot = self.current
        water = await FarmService().water_seed(self.author.id, slot)
        if water:
            self.farm[slot - 1]["is_watered"] = True
            await self.rewrite_view(interaction)
        else:
            await self.rewrite_view(interaction)

    async def seed_button_callback(self, interaction: discord.Interaction):
        """
        일단 당근으로 채워서 테스팅 후 심을 작물 고르는 화면으로 변경
        """
        print("?")
        slot = self.current
        seed = await FarmService().plant_seed(self.author.id, slot, "당근")
        if seed:
            await self.rewrite_view(interaction)
        else:
            await self.rewrite_view(interaction)

    async def harvest_button_callback(self, interaction: discord.Interaction):
        slot = self.current

        harvest = await FarmService().harvest_crop(self.author.id, slot)
        if harvest:

            self.farm[slot - 1] = {
                "slot_id": slot,
                "crop_id": None,
                "is_watered": None,
                "planted_at": None,
            }
            await self.rewrite_view(interaction)
        else:
            await self.rewrite_view(interaction)

    async def select_callback(self, interaction: discord.Interaction):
        selected_slot = interaction.data["values"][0]
        self.current = int(selected_slot)
        await interaction.response.send_message(
            f"{selected_slot}번 땅이 선택되었습니다.", ephemeral=True, delete_after=3
        )

    async def prev_page_callback(self, interaction: discord.Interaction):
        new_view = FarmView(self.farm, self.author, page=self.page - 1)
        embed = self.create_embed(new_view)
        await interaction.response.edit_message(embed=embed, view=new_view)

    async def next_page_callback(self, interaction: discord.Interaction):
        new_view = FarmView(self.farm, self.author, page=self.page + 1)
        embed = self.create_embed(new_view)
        await interaction.response.edit_message(embed=embed, view=new_view)

    def create_embed(self, view: "FarmView") -> discord.Embed:
        embed = discord.Embed(
            title=f"🌾 농장 정보 (페이지 {view.page}/{view.total_pages})",
            color=discord.Color.green(),
        )
        if not view.current_items:
            embed.description = "농장에 등록된 데이터가 없습니다."
        else:
            description_lines = []
            for item in view.current_items:
                slot_id = item["slot_id"]
                crop_name = item["crop_id"] if item["crop_id"] else "없음"
                time = item["planted_at"]
                description_lines.append(
                    f"• **{slot_id}번 땅**: {crop_name}"
                    + (f"<t:{time}:R>" if crop_name != "없음" else "")
                )
            embed.description = "\n".join(description_lines)

        return embed
