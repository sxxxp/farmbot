from discord import Interaction, app_commands
from discord.ext import commands

from service.warehouse import WarehouseService
from ui.views import WarehouseView
from utils.errorcheck import is_registered


class Warehouse(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="창고", description="보유한 작물·씨앗·비료를 확인하고 수확물을 판매합니다.")
    @is_registered()
    async def warehouse(self, interaction: Interaction):
        board = await WarehouseService().get_warehouse(interaction.user.id)
        view = WarehouseView(interaction.user, board)
        await interaction.response.send_message(embed=view.create_embed(), view=view)


async def setup(bot):
    await bot.add_cog(Warehouse(bot))
