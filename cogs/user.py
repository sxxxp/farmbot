from discord import app_commands, Interaction, Member
from discord.ext import commands
from service.user import UserService
from ui.embed import embed_profile


class User(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="프로필")
    async def profile(self, interaction: Interaction, user: Member):
        profile = await UserService().get_user(user.id)
        embed = embed_profile(profile)

        await interaction.response.send_message(f"프로필입니다.", embed=embed)

    @app_commands.command(name="회원가입")
    async def register(self, interaction: Interaction):
        user = await UserService().create_user(interaction.user.id)
        if user:
            return await interaction.response.send_message("회원가입에 성공했습니다!")
        await interaction.response.send_message("회원가입에 실패했습니다!")


async def setup(bot):
    await bot.add_cog(User(bot))
