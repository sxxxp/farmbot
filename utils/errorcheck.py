import discord
from discord import app_commands
from database.connection import db
from service.user import UserService


class UserNotRegistered(app_commands.AppCommandError):
    """유저가 DB에 없을 때 발생시킬 커스텀 예외"""

    def __init__(
        self,
        message: str = "가입되지 않은 유저입니다. `/회원가입`을 먼저 진행해 주세요.",
    ):
        self.message = message
        super().__init__(self.message)


async def is_registered_user(interaction: discord.Interaction) -> bool:

    user = await UserService(db).get_user(interaction.user.id)

    if not user:
        raise UserNotRegistered()

    return True
