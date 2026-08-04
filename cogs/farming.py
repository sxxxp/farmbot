import discord
from discord.ext import commands
from utils.embed import embed_crop_info


class Farming(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="농사")
    async def farm_status(self, ctx: commands.Context):
        await ctx.send(f"{ctx.author.mention}님의 밭 상태입니다! 🌾")

    @commands.command(name="작물")
    async def crop_info(self, ctx: commands.Context, crop_name: str):
        embed = embed_crop_info(crop_name)
        await ctx.send(
            f"{ctx.author.mention}님, {crop_name}에 대한 정보입니다! 🌱", embed=embed
        )


async def setup(bot):
    await bot.add_cog(Farming(bot))
