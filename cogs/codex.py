import discord
from discord.ext import commands

from utils.crop import num_of_crops


class Codex(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="도감", description="작물 도감을 확인합니다.")
    async def codex(self, ctx: commands.Context):

        await ctx.send(
            f"{ctx.author.mention}님의 도감 현황 입니다. {0}/{num_of_crops()} 🌱"
        )


async def setup(bot):
    await bot.add_cog(Codex(bot))
