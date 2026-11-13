from discord import Interaction, app_commands
from discord.ext import commands

from ui.views import build_game_screen
from utils.errorcheck import is_registered


class Game(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="게임창", description="농장·상점·제작소·창고·도감·패시브·퀘스트를 한 창에서 오갑니다."
    )
    @is_registered()
    async def game(self, interaction: Interaction):
        embed, view = await build_game_screen("home", interaction.user)
        await interaction.response.send_message(embed=embed, view=view)


async def setup(bot):
    await bot.add_cog(Game(bot))
