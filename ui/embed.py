import discord
from service.crop import get_crop_data
from utils.datatype import UserData


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
    embed.add_field(name="작물 종류", value=f"{crop_data['crop_type']}", inline=True)
    return embed


def embed_shop(user: UserData):
    embed = discord.Embed(
        title="상점",
        color=discord.Color.blue(),
    )
    embed.add_field(name="농장 레벨", value=f"{user['farm_level']}레벨", inline=False)
    embed.add_field(name="보유 금액", value=f"{user['gold']}원", inline=False)
    return embed


def embed_profile(user: UserData):
    embed = discord.Embed(
        title="프로필",
        color=discord.Color.purple(),
    )
    embed.add_field(name="농장 레벨", value=f"{user['farm_level']}레벨", inline=False)
    embed.add_field(name="총 경험치", value=f"{user['farm_exp']}xp", inline=False)
    embed.add_field(name="보유 금액", value=f"{user['gold']}원", inline=False)
    return embed
