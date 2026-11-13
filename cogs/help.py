import discord
from discord import Interaction, app_commands
from discord.ext import commands

from utils.command_mention import command_mention

# 분류와 표시 순서. 여기에 없는 명령어는 '기타' 에 이름 순으로 표시된다.
CATEGORIES: list[tuple[str, list[str]]] = [
    ("🚀 시작하기", ["회원가입", "게임창", "명령어", "프로필"]),
    ("🌾 농사", ["농사", "작물", "도감"]),
    ("💰 거래 · 제작", ["상점", "창고", "제작"]),
    ("📈 성장", ["퀘스트", "패시브"]),
]
OTHER_CATEGORY = "📦 기타"


def is_admin_command(command: app_commands.Command) -> bool:
    """utils.errorcheck.is_owner() 체크가 붙은 명령어"""
    return any(getattr(check, "admin_only", False) for check in command.checks)


def public_commands(tree: app_commands.CommandTree) -> list[app_commands.Command]:
    return [
        command
        for command in tree.get_commands()
        if isinstance(command, app_commands.Command) and not is_admin_command(command)
    ]


def build_help_embed(commands_: list[app_commands.Command]) -> discord.Embed:
    by_name = {command.name: command for command in commands_}
    embed = discord.Embed(
        title="📖 명령어 목록",
        description="명령어를 누르면 채팅창에 바로 입력됩니다.",
        color=discord.Color.blurple(),
    )

    def line(command: app_commands.Command) -> str:
        description = command.description if command.description != "…" else ""
        return f"{command_mention(command.name)} — {description}".rstrip(" —")

    listed: set[str] = set()
    for title, names in CATEGORIES:
        lines = [line(by_name[name]) for name in names if name in by_name]
        listed.update(names)
        if lines:
            embed.add_field(name=title, value="\n".join(lines), inline=False)

    others = sorted(name for name in by_name if name not in listed)
    if others:
        embed.add_field(
            name=OTHER_CATEGORY,
            value="\n".join(line(by_name[name]) for name in others),
            inline=False,
        )
    return embed


class Help(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="명령어", description="사용할 수 있는 명령어 목록을 봅니다.")
    async def help(self, interaction: Interaction):
        embed = build_help_embed(public_commands(self.bot.tree))
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Help(bot))
