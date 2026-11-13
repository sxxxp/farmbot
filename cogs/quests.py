from discord import Interaction, app_commands
from discord.ext import commands

from service.quest import QuestService
from ui.views import QuestView
from utils.errorcheck import is_registered


class Quests(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="퀘스트", description="메인/일일/주간 퀘스트를 확인합니다.")
    @is_registered()
    async def quests(self, interaction: Interaction):
        board = await QuestService().get_quests(interaction.user.id, "main")
        view = QuestView(interaction.user, board)
        await interaction.response.send_message(embed=view.create_embed(), view=view)


async def setup(bot):
    await bot.add_cog(Quests(bot))
