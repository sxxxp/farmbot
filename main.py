import os
import asyncio
import traceback
from database.connection import db
import discord

from discord.ext import commands
import config
from utils.errorcheck import UserNotRegistered

intents = discord.Intents.default()
intents.message_content = True


class FarmBot(commands.Bot):
    def __init__(self):
        super().__init__(
            command_prefix="!",
            intents=intents,
            help_command=None,
            owner_ids={432066597591449600},
        )

    async def setup_hook(self):
        print("데이터베이스 연결을 시작합니다...")
        await db.connect()
        print("Cogs 로드를 시작합니다...")
        flag = True
        for filename in os.listdir("./cogs"):
            if filename.endswith(".py") and not filename.startswith("_"):
                extension_name = f"cogs.{filename[:-3]}"
                try:
                    await self.load_extension(extension_name)
                    print(f"✅ 로드 성공: {extension_name}")
                except Exception as e:
                    flag = False
                    print(f"❌ 로드 실패: {extension_name} - 오류: {e}")
                    print("=" * 40)
                    traceback.print_exc()
                    print("=" * 40)
        print(f"Cogs 로드가 {'완료되었습니다.' if flag else '실패했습니다. ⚠️'}")
        self.tree.copy_global_to(guild=discord.Object(id=1116522262426824756))
        synced = await self.tree.sync(guild=discord.Object(id=1116522262426824756))
        print(f"{len(synced)}개의 명령어 동기화가 완료되었습니다.")

    async def close(self):
        print("데이터베이스 연결이 종료되었습니다.")
        await db.close()
        print("봇이 종료됩니다.")
        await super().close()

    async def on_ready(self):
        print(f"--- 로그인 완료 ---")
        print(f"봇 이름: {self.user.name}")
        print(f"봇 ID: {self.user.id}")
        print(f"-------------------")


async def main():
    bot = FarmBot()

    @bot.tree.error
    async def on_app_comamnd_error(
        interaction: discord.Interaction, error: discord.app_commands.AppCommandError
    ):
        if isinstance(error, UserNotRegistered):
            msg = error.message if hasattr(error, "message") else str(error)
            if not interaction.response.is_done():
                await interaction.response.send_message(msg, ephemeral=True)
            else:
                await interaction.followup.send(msg, ephemeral=True)
            return
        await interaction.response.send_message(
            "예기치 못한 에러가 발생했어요.", ephemeral=True
        )
        print(f"Unhandled error: {error}")

    await bot.start(config.TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
