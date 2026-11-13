from discord import Interaction, app_commands
from discord.ext import commands

from service.codex import CodexService
from ui.views import CodexView
from utils.errorcheck import is_registered


class Codex(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="도감", description="수확한 작물 도감을 일반·황금·돌연변이로 나눠서 봅니다."
    )
    @is_registered()
    async def codex(self, interaction: Interaction):
        board = await CodexService().get_codex(interaction.user.id)
        view = CodexView(interaction.user, board)
        await interaction.response.send_message(embed=view.create_embed(), view=view)


async def setup(bot):
    await bot.add_cog(Codex(bot))
