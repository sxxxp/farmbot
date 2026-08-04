import discord
from utils.crop import get_crop_data


def embed_crop_info(crop_name: str) -> discord.Embed:
    crop_data = get_crop_data(crop_name)
    embed = discord.Embed(
        title=f"{crop_name} 정보",
        description=crop_data.get("description", "정보가 없습니다."),
        color=discord.Color.green(),
    )
    embed.add_field(name="성장 시간", value=f"{crop_data['grow_time']}초", inline=True)
    embed.add_field(name="구매 가격", value=f"{crop_data['buy_price']}원", inline=True)
    embed.add_field(name="판매 가격", value=f"{crop_data['sell_price']}원", inline=True)
    embed.add_field(name="구매 등급", value=f"{crop_data['buy_grade']}", inline=True)
    return embed
