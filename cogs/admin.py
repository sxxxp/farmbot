import traceback

import discord
from discord.ext import commands
import consistances
from discord import app_commands
from service.user import UserService
from ui.notice import send_notice
from utils.errorcheck import is_owner, is_target_registered


class Admin(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="아이템넣기", description="[관리자] 유저에게 아이템을 지급합니다."
    )
    @is_owner()
    @is_target_registered("유저")
    async def giveItem(
        self,
        interaction: discord.Interaction,
        유저: discord.Member,
        아이템: str,
        개수: int,
    ):
        flag = await UserService().give_item(유저.id, 아이템, 개수)
        if flag:
            msg = f"{유저.id}님께 {아이템}을 {개수}개 만큼 추가했습니다."
        else:
            msg = f"{유저.id}님께 {아이템}을 주는데 실패했습니다."
        await send_notice(interaction, msg, ephemeral=False)

    @app_commands.command(
        name="정적데이터재로드", description="[관리자] 정적 데이터를 재로드합니다."
    )
    @is_owner()
    async def reloadStatic(self, interaction: discord.Interaction):
        try:
            consistances.reload_static_data()
        except Exception as e:
            traceback.print_exc()
            await send_notice(
                interaction,
                f"정적 데이터 재로드에 실패했습니다. 기존 데이터를 유지합니다.\n`{type(e).__name__}: {e}`",
                ephemeral=True,
            )
            return
        await send_notice(
            interaction,
            f"정적 데이터를 재로드했습니다. (작물 {consistances.NUM_OF_CROPS}종)",
            ephemeral=True,
        )


async def setup(bot):
    await bot.add_cog(Admin(bot))
