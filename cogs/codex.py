from discord import Interaction, Interaction, app_commands
from discord.ext import commands

from consistances import NUM_OF_CROPS


class Codex(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="도감", description="작물 도감을 확인합니다.")
    async def codex(self, interaction: Interaction):
        await interaction.response.send_message(
            f"{interaction.user.mention}님의 도감 현황 입니다. {0}/{NUM_OF_CROPS} 🌱"
        )


async def setup(bot):
    await bot.add_cog(Codex(bot))
