from discord import Interaction, app_commands
from discord.ext import commands

from service.craft import CraftService
from ui.views import CraftView
from utils.errorcheck import is_registered


class Craft(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="제작", description="수확물로 비료·씨앗을 제작합니다.")
    @is_registered()
    async def craft(self, interaction: Interaction):
        board = await CraftService().get_board(interaction.user.id)
        view = CraftView(interaction.user, board)
        await interaction.response.send_message(embed=view.create_embed(), view=view)


async def setup(bot):
    await bot.add_cog(Craft(bot))
