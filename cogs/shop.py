from discord import Interaction, app_commands
from discord.ext import commands

from service.shop import ShopService
from ui.views import ShopView
from utils.errorcheck import is_registered


class Shop(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="상점", description="씨앗과 비료를 구매합니다. (3시간마다 입고)")
    @is_registered()
    async def shop(self, interaction: Interaction):
        board = await ShopService().get_shop(interaction.user.id)
        view = ShopView(interaction.user, board)
        await interaction.response.send_message(embed=view.create_embed(), view=view)


async def setup(bot):
    await bot.add_cog(Shop(bot))
