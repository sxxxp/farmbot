from discord import app_commands, Interaction
from discord.ext import commands


class Shop(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="상점")
    async def shop(self, interaction: Interaction):
        await interaction.response.send_message(f"상점입니다.")


async def setup(bot):
    await bot.add_cog(Shop(bot))
