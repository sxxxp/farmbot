from discord import app_commands, Interaction, Member
from discord.ext import commands
from service.user import UserService
from ui.embed import embed_profile
from utils.command_mention import command_mention
from utils.errorcheck import is_target_registered


class User(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="프로필", description="유저의 농장 레벨·경험치·골드를 확인합니다.")
    @is_target_registered()
    async def profile(self, interaction: Interaction, user: Member):
        profile = await UserService().get_user(user.id)
        embed = embed_profile(profile, user.display_name)
        embed.set_thumbnail(url=user.display_avatar.url)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="회원가입", description="농장을 시작합니다.")
    async def register(self, interaction: Interaction):
        user = await UserService().create_user(interaction.user.id)
        if user:
            return await interaction.response.send_message(
                "회원가입에 성공했습니다! 🌱\n"
                f"{command_mention('퀘스트')} 를 입력해서 첫 퀘스트를 확인해 보세요."
            )
        await interaction.response.send_message("회원가입에 실패했습니다!")


async def setup(bot):
    await bot.add_cog(User(bot))
