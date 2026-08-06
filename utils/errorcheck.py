import discord
from discord import app_commands
from database.connection import db
from service.user import UserService


class UserNotRegistered(app_commands.AppCommandError):
    """유저가 DB에 없을 때 발생시킬 커스텀 예외"""

    pass


async def is_registered_user(interaction: discord.Interaction) -> bool:

    user = await UserService(db).get_user(interaction.user.id)

    if not user:
        # DB에 없다면 예외를 발생시킴
        raise UserNotRegistered(
            "가입되지 않은 유저입니다. `/회원가입`을 먼저 진행해 주세요."
        )

    return True
