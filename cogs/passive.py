from discord import Interaction, app_commands
from discord.ext import commands

from service.passive import PassiveService
from ui.views import PassiveView
from utils.errorcheck import is_registered


class Passive(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="패시브", description="레벨업 포인트로 패시브 트리를 찍습니다.")
    @is_registered()
    async def passive(self, interaction: Interaction):
        board = await PassiveService().get_board(interaction.user.id)
        view = PassiveView(interaction.user, board)
        await interaction.response.send_message(embed=view.create_embed(), view=view)


async def setup(bot):
    await bot.add_cog(Passive(bot))
