import discord
from discord.ext import commands
from discord import app_commands
from service.user import UserService
from utils.errorcheck import is_owner, is_target_registered


class Admin(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="아이템넣기")
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
            return await interaction.response.send_message(
                f"{유저.id}님께 {아이템}을 {개수}개 만큼 추가했습니다."
            )
        else:
            return await interaction.response.send_message(
                f"{유저.id}님께 {아이템}을 주는데 실패했습니다."
            )


async def setup(bot):
    await bot.add_cog(Admin(bot))
