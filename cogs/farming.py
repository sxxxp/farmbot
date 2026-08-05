from discord import app_commands, Interaction
from discord.ext import commands
from consistances import get_crop_choice
from utils.embed import embed_crop_info


class Farming(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="농사")
    async def farm_status(self, interaction: Interaction):
        await interaction.response.send_message(
            f"{interaction.user.mention}님의 밭 상태입니다! 🌾"
        )

    @app_commands.command(name="작물")
    async def crop_info(self, interaction: Interaction, crop_name: get_crop_choice):
        embed = embed_crop_info(crop_name)
        await interaction.response.send_message(
            f"{interaction.user.mention}님, {crop_name}에 대한 정보입니다! 🌱",
            embed=embed,
        )


async def setup(bot):
    await bot.add_cog(Farming(bot))
