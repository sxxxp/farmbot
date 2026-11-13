import discord
from discord import app_commands
from service.user import UserService
from exceptions import UserNotAdmin, UserNotRegistered


def _is_registered(msg: str):
    async def predicate(interaction: discord.Interaction) -> bool:
        user = await UserService().is_user_exist(interaction.user.id)

        if not user:
            raise UserNotRegistered(interaction.user, msg)

        return True

    return app_commands.check(predicate)


def is_registered():
    return _is_registered(None)


def is_target_registered(param_name: str = "user"):
    async def predicate(interaction: discord.Interaction) -> bool:
        target_member = getattr(interaction.namespace, param_name, None)
        if target_member is None:
            # 대상이 지정되지 않은 경우(선택 파라미터 등)는 통과
            return True

        if not await UserService().is_user_exist(target_member.id):
            raise UserNotRegistered(
                interaction.user, "해당 유저의 프로필이 존재하지 않습니다."
            )

        return True

    return app_commands.check(predicate)


def is_owner():
    async def predicate(interaction: discord.Interaction) -> bool:
        if await interaction.client.is_owner(interaction.user):
            return True
        raise UserNotAdmin

    predicate.admin_only = True  # /명령어 목록에서 제외하는 표시
    return app_commands.check(predicate)
