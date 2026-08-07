from discord import app_commands, Interaction
from discord.ext import commands
from consistances import CROPS_DATA
from service.farm import FarmService
from ui.embed import embed_crop_info
from ui.views import FarmView
from utils.errorcheck import is_registered


class Farming(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def crop_autocomplete(
        self, interaction: Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return [
            app_commands.Choice(name=crop_id, value=crop_id)
            for crop_id in CROPS_DATA.keys()
            if current.lower() in crop_id.lower()
        ][:25]

    @app_commands.command(name="농사")
    @is_registered()
    async def farm_status(self, interaction: Interaction):
        farmService = FarmService()
        farm = await farmService.get_farm(interaction.user.id)
        view = FarmView(farm, interaction.user, page=1)
        embed = view.create_embed(view)
        await interaction.response.send_message(embed=embed, view=view)

    @app_commands.command(name="작물")
    @app_commands.autocomplete(작물이름=crop_autocomplete)
    async def crop_info(self, interaction: Interaction, 작물이름: str):
        embed = embed_crop_info(작물이름)
        await interaction.response.send_message(
            f"{interaction.user.mention}님, {작물이름}에 대한 정보입니다! 🌱",
            embed=embed,
        )


async def setup(bot):
    await bot.add_cog(Farming(bot))
