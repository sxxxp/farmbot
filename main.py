import os
import asyncio
import traceback
import discord
from discord.ext import commands
import config

intents = discord.Intents.default()
intents.message_content = True


class FarmBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents, help_command=None)

    async def setup_hook(self):
        print("Cogs 로드를 시작합니다...")

        for filename in os.listdir("./cogs"):
            if filename.endswith(".py") and not filename.startswith("_"):
                extension_name = f"cogs.{filename[:-3]}"
                try:
                    await self.load_extension(extension_name)
                    print(f"✅ 로드 성공: {extension_name}")
                except Exception as e:
                    print(f"❌ 로드 실패: {extension_name} - 오류: {e}")
                    print("=" * 40)
                    traceback.print_exc()
                    print("=" * 40)

    async def on_ready(self):
        print(f"--- 로그인 완료 ---")
        print(f"봇 이름: {self.user.name}")
        print(f"봇 ID: {self.user.id}")
        print(f"-------------------")


async def main():
    bot = FarmBot()
    await bot.start(config.TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
