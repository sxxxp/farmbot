from typing import List

import discord
from consistances import (
    CROPS_DATA,
    FERTILIZERS,
    GRADE_NAMES,
    HARVEST_CONFIG,
    PASSIVE_CONFIG,
    EVENT_GRADE,
    difficulty_name,
    grade_name,
)
from service.crop import get_crop_data
from service.produce import (
    MAX_STARS,
    star_exp,
    parse_produce_id,
    produce_display_name,
    rot_after_seconds,
    stars_text,
)
from service.user import UserService
from utils.datatype import (
    CodexBoard,
    CraftBoard,
    CraftRecipeStatus,
    FarmData,
    PassiveBoard,
    QuestBoard,
    QuestReward,
    QuestStatus,
    ShopBoard,
    ShopItem,
    UserData,
    WarehouseBoard,
)


# ------------------------------------------------------------------ 공통 표기


def won(amount: int | float) -> str:
    """1234 → '1,234원' (천 단위 구분)"""
    return f"{int(round(amount)):,}원"


def user_header(
    user_name: str, level: int | None = None, gold: int | None = None
) -> str:
    """모든 화면 맨 위에 같은 모양으로 쓰는 한 줄: '👤 **이름** · Lv.**3** · 💰 **1,234원**'"""
    parts = [f"👤 **{user_name}**"]
    if level is not None:
        parts.append(f"Lv.**{level}**")
    if gold is not None:
        parts.append(f"💰 **{won(gold)}**")
    return " · ".join(parts)


def exp_range_text(base_exp: int) -> str:
    """수확 경험치 범위: ★1 ~ ★5 (예: '10~25xp')"""
    low, high = star_exp(base_exp, 1), star_exp(base_exp, MAX_STARS)
    return f"{low:,}xp" if low == high else f"{low:,}~{high:,}xp"


def level_progress(exp: int, level: int) -> str:
    """'`▰▰▰▱▱▱▱▱▱▱` 400 / 900xp · 다음 레벨까지 500xp'"""
    start, end = UserService.level_exp_range(level)
    gained, need = exp - start, end - start
    return (
        f"`{progress_bar(gained, need)}` {exp:,} / {end:,}xp"
        f" · 다음 레벨까지 **{end - exp:,}xp**"
    )


# ------------------------------------------------------------------ 작물 정보 / 프로필


def embed_crop_info(crop_name: str) -> discord.Embed | None:
    """작물 정보 embed. 없는 작물이면 None"""
    crop_data = get_crop_data(crop_name)
    if not crop_data:
        return None
    event = crop_data["buy_grade"] == EVENT_GRADE
    embed = discord.Embed(
        title=f"🌱 {crop_name}",
        description=f"-# {crop_data.get('description', '정보가 없습니다.')}",
        color=discord.Color.green(),
    )
    # 3열 x 3줄: 분류 / 시간 / 돈
    embed.add_field(
        name="씨앗 등급",
        value=grade_name(crop_data["buy_grade"]) + (" (상점 미판매)" if event else ""),
        inline=True,
    )
    embed.add_field(name="작물 종류", value=f"{crop_data['crop_type']}", inline=True)
    embed.add_field(name="재배 난이도", value=difficulty_name(crop_data["difficulty"]), inline=True)
    embed.add_field(name="⏱️ 성장 시간", value=format_duration(crop_data["grow_time"]), inline=True)
    embed.add_field(
        name="🥀 썩기까지", value=f"다 자란 뒤 {format_duration(rot_after_seconds(crop_name))}",
        inline=True,
    )
    embed.add_field(name="✨ 경험치 (★1~★5)", value=exp_range_text(crop_data["exp"]), inline=True)
    embed.add_field(
        name="💰 씨앗 가격", value="-" if event else won(crop_data["buy_price"]), inline=True
    )
    embed.add_field(name="💰 판매가 (★1)", value=won(crop_data["sell_price"]), inline=True)
    if not event:
        embed.add_field(
            name="📈 1회 수익",
            value=f"+{won(crop_data['sell_price'] - crop_data['buy_price'])}",
            inline=True,
        )
    return embed


def embed_profile(user: UserData, user_name: str | None = None) -> discord.Embed:
    embed = discord.Embed(
        title=f"👤 {user_name}님의 프로필" if user_name else "👤 프로필",
        color=discord.Color.purple(),
    )
    embed.add_field(name="🌾 농장 레벨", value=f"Lv.**{user['level']}**", inline=True)
    embed.add_field(name="💰 보유 골드", value=f"**{won(user['gold'])}**", inline=True)
    embed.add_field(
        name="✨ 경험치", value=level_progress(user["farm_exp"], user["level"]), inline=False
    )
    return embed


QUEST_TAB_TITLES = {
    "main": "📜 메인 퀘스트",
    "daily": "📅 일일 퀘스트",
    "weekly": "🗓️ 주간 퀘스트",
}

OBJECTIVE_UNITS = {"plant": "회 심기", "harvest": "회 수확", "water": "회 물주기"}


def item_display_name(item_id: str) -> str:
    """'당근_seed' → '당근 씨앗', '중급_비료' → '중급 비료', '당근|3' → '당근 ★★★☆☆'"""
    if item_id.endswith("_seed"):
        return f"{item_id[: -len('_seed')]} 씨앗"
    if item_id in FERTILIZERS:
        return FERTILIZERS[item_id]["name"]
    if parse_produce_id(item_id) is not None:
        return produce_display_name(item_id)
    return item_id


def fertilizer_effect_text(item_id: str) -> str:
    """'⏱️ 성장 시간 5분 단축' / '🌟 수확 별 확률 ↑' (둘 다 있으면 같이)"""
    data = FERTILIZERS[item_id]
    parts = []
    if data.get("reduce_minutes"):
        parts.append(f"⏱️ 성장 시간 {data['reduce_minutes']}분 단축")
    if data.get("quality_bonus"):
        parts.append("🌟 수확 별 확률 ↑")
    return " · ".join(parts)


def format_duration(seconds: int) -> str:
    """150 → '2분 30초', 3600 → '1시간'"""
    hours, rest = divmod(int(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    parts = []
    if hours:
        parts.append(f"{hours}시간")
    if minutes:
        parts.append(f"{minutes}분")
    if secs or not parts:
        parts.append(f"{secs}초")
    return " ".join(parts)


def format_reward(reward: QuestReward) -> str:
    parts = []
    if reward.get("gold"):
        parts.append(f"💰 {won(reward['gold'])}")
    if reward.get("exp"):
        parts.append(f"✨ {reward['exp']}xp")
    for item_id, amount in reward.get("items", {}).items():
        parts.append(f"🎒 {item_display_name(item_id)} x{amount}")
    return " · ".join(parts) if parts else "없음"


def progress_bar(progress: int, total: int, length: int = 10) -> str:
    filled = round(length * progress / total) if total else length
    return "▰" * filled + "▱" * (length - filled)


def _quest_field(status: QuestStatus) -> tuple[str, str]:
    quest = status["quest"]
    objective = quest["objective"]

    if status["locked"]:
        icon = "🔒"
    elif status["claimed"]:
        icon = "✅"
    elif status["completed"]:
        icon = "🎁"
    else:
        icon = "▫️"

    if status["locked"]:
        progress_line = f"🔒 농장 레벨 {quest['min_level']} 이상 필요"
    elif objective["type"] == "level":
        progress_line = (
            f"`{progress_bar(status['progress'], objective['count'])}` "
            f"레벨 {status['progress']}/{objective['count']}"
        )
    else:
        # 특정 작물 > 작물 종류 > 아무 작물
        label = objective.get("target") or objective.get("crop_type")
        target = f"{label} " if label else ""
        if objective.get("min_stars"):
            target += f"★{objective['min_stars']} 이상 "
        unit = OBJECTIVE_UNITS.get(objective["type"], "회")
        progress_line = (
            f"`{progress_bar(status['progress'], objective['count'])}` "
            f"{target}{status['progress']}/{objective['count']}{unit}"
        )

    value = "\n".join(
        [quest["description"], progress_line, f"보상: {format_reward(quest['reward'])}"]
    )
    return f"{icon} {quest['title']}", value


def embed_quests(board: QuestBoard, user_name: str) -> discord.Embed:
    quest_type = board["quest_type"]
    embed = discord.Embed(
        title=QUEST_TAB_TITLES[quest_type],
        color=discord.Color.gold(),
    )

    lines = [user_header(user_name, board["level"])]
    if quest_type == "main":
        lines.append(f"메인 퀘스트 진행: {board['main_cleared']}/{board['main_total']}")
    if board["reset_at"]:
        lines.append(f"⏰ 초기화 <t:{board['reset_at']}:R>")
    embed.description = "\n".join(lines)

    if not board["quests"]:
        if quest_type == "main":
            message = "🎉 모든 메인 퀘스트를 완료했습니다!"
        else:
            message = "현재 레벨에서 받을 수 있는 퀘스트가 없습니다."
        embed.add_field(name="\u200b", value=message, inline=False)

    for status in board["quests"]:
        name, value = _quest_field(status)
        embed.add_field(name=name, value=value, inline=False)

    embed.set_footer(text="🎁 완료 · ✅ 보상 수령 · 🔒 잠김")
    return embed


# ------------------------------------------------------------------ 상점


def shop_item_lines(item: ShopItem) -> tuple[str, str]:
    """상점 품목 embed 필드 (이름, 내용)"""
    name = item_display_name(item["item_id"])
    grade = grade_name(item["grade"])
    icon = "🌱" if item["category"] == "seed" else "🧪"
    remaining = item["stock"] - item["bought"]

    if item["category"] == "seed":
        crop = CROPS_DATA[item["item_id"].removesuffix("_seed")]
        detail = f"⏱️ 성장 {format_duration(crop['grow_time'])} · ✨ {exp_range_text(crop['exp'])}"
    else:
        detail = fertilizer_effect_text(item["item_id"])

    stock_text = "**품절**" if remaining <= 0 else f"재고 {remaining}/{item['stock']}"
    title = f"{icon} {name} · {grade}"
    if remaining <= 0:
        title = f"~~{title}~~"
    return title, f"💰 {won(item['price'])} · {stock_text}\n{detail}"


def format_odds(odds: dict[int, float]) -> str:
    return " · ".join(f"{grade_name(g)} {p:.0f}%" for g, p in sorted(odds.items()))


def embed_shop(
    board: ShopBoard,
    user_name: str,
    odds: dict[str, dict[int, float]],
    selected: int | None = None,
) -> discord.Embed:
    """상점 embed. odds: 카테고리 -> 등급 -> 확률(%)"""
    embed = discord.Embed(title="🏪 상점", color=discord.Color.blue())
    embed.description = (
        f"{user_header(user_name, board['level'], board['gold'])}\n"
        f"⏰ 다음 입고 <t:{board['reset_at']}:R> (<t:{board['reset_at']}:t>)"
    )
    if board.get("discount_pct"):
        embed.description += f"\n🏷️ 패시브 할인 **{board['discount_pct']:g}%** 적용 중"

    if not board["items"]:
        embed.add_field(name="​", value="진열된 품목이 없습니다.", inline=False)
    for item in board["items"]:
        title, value = shop_item_lines(item)
        if item["slot"] == selected:
            title = f"▶ {title}"
        embed.add_field(name=title, value=value, inline=False)

    embed.add_field(
        name=f"📊 레벨 {board['level']} 등급 확률",
        value=(
            f"🌱 씨앗: {format_odds(odds['seed'])}\n"
            f"🧪 비료: {format_odds(odds['fertilizer'])}"
        ),
        inline=False,
    )
    embed.set_footer(text="레벨이 오를수록 좋은 등급이 나올 확률이 올라갑니다.")
    return embed


# ------------------------------------------------------------------ 창고

WAREHOUSE_PAGE_SIZE = 10


def _join_limited(parts: list[str], limit: int = 12) -> str:
    if len(parts) <= limit:
        return ", ".join(parts)
    return ", ".join(parts[:limit]) + f" 외 {len(parts) - limit}종"


def embed_warehouse(
    board: WarehouseBoard, user_name: str, page: int = 1, selected: str | None = None
) -> discord.Embed:
    produce = board["produce"]
    total_pages = max(1, -(-len(produce) // WAREHOUSE_PAGE_SIZE))
    page = min(max(page, 1), total_pages)

    embed = discord.Embed(title="📦 창고", color=discord.Color.dark_gold())
    total_value = sum(p["price"] * p["amount"] for p in produce)
    embed.description = (
        f"{user_header(user_name, gold=board['gold'])}\n"
        f"🧺 수확물 전체 판매가: **{won(total_value)}**"
    )

    start = (page - 1) * WAREHOUSE_PAGE_SIZE
    lines = []
    for item in produce[start : start + WAREHOUSE_PAGE_SIZE]:
        marker = "▶" if item["item_id"] == selected else "•"
        lines.append(
            f"{marker} {produce_display_name(item['item_id'])} x{item['amount']}"
            f" · 개당 {won(item['price'])}"
        )
    embed.add_field(
        name=f"🧺 수확물 ({page}/{total_pages})",
        value="\n".join(lines) if lines else "수확한 작물이 없습니다.",
        inline=False,
    )

    seeds = [f"{name} x{amount}" for name, amount in board["seeds"]]
    embed.add_field(
        name=f"🌱 씨앗 ({len(seeds)}종)",
        value=_join_limited(seeds) if seeds else "없음",
        inline=False,
    )
    fertilizers = [f"{item_display_name(i)} x{a}" for i, a in board["fertilizers"]]
    embed.add_field(
        name="🧪 비료",
        value=", ".join(fertilizers) if fertilizers else "없음",
        inline=False,
    )
    embed.set_footer(
        text="별이 많을수록, 🧬돌연변이일수록 비싸게 팔립니다. "
        "물·비료·제때 수확이 별을 올려 줍니다."
    )
    return embed


# ------------------------------------------------------------------ 패시브

EFFECT_TEXT = {
    "sell_price_pct": lambda v: f"수확물 판매가 +{v:g}%",
    "shop_discount_pct": lambda v: f"상점 할인 {v:g}%",
    "care_points": lambda v: f"수확 품질 점수 +{v:g}",
    "mutation_chance": lambda v: f"돌연변이 확률 +{round(v * 100, 2):g}%p",
    "golden_chance": lambda v: f"황금 작물 확률 +{round(v * 100, 2):g}%p",
    "craft_material_pct": lambda v: f"제작 재료 -{v:g}%",
    "craft_gold_pct": lambda v: f"제작 골드 비용 -{v:g}%",
    "grow_time_pct": lambda v: f"작물 성장 시간 -{v:g}%",
    "exp_pct": lambda v: f"수확 경험치 +{v:g}%",
}


def effect_text(effect: str, value: float) -> str:
    formatter = EFFECT_TEXT.get(effect)
    return formatter(value) if formatter else f"{effect} {value:g}"


def embed_passives(
    board: PassiveBoard, user_name: str, selected: str | None = None
) -> discord.Embed:
    embed = discord.Embed(title="🌳 패시브 트리", color=discord.Color.teal())
    embed.description = (
        f"{user_header(user_name, board['level'])}\n"
        f"🔹 남은 포인트 **{board['available_points']}** "
        f"(사용 {board['spent_points']}/{board['total_points']}) · "
        f"초기화 비용 {won(board['reset_cost'])}\n"
        f"-# 레벨이 오를 때마다 {PASSIVE_CONFIG['points_per_level']}포인트를 얻습니다."
    )

    names = {status["node"]["id"]: status["node"]["name"] for status in board["nodes"]}
    for branch in PASSIVE_CONFIG["branches"]:
        lines = []
        for status in board["nodes"]:
            node = status["node"]
            if node["branch"] != branch["id"]:
                continue
            if status["maxed"]:
                icon = "✅"
            elif not status["unlocked"]:
                icon = "🔒"
            elif status["rank"]:
                icon = "🔷"
            else:
                icon = "◽"
            marker = "▶ " if node["id"] == selected else ""
            line = (
                f"{marker}{icon} **{node['name']}** [{status['rank']}/{node['max_rank']}]"
                f" — {node['description']}"
            )
            if not status["unlocked"]:
                needs = ", ".join(
                    f"{names.get(req['node'], req['node'])} {req['rank']}"
                    for req in node["requires"]
                )
                line += f"\n　-# 필요: {needs}"
            lines.append(line)
        embed.add_field(
            name=f"{branch['emoji']} {branch['name']}", value="\n".join(lines), inline=False
        )

    effects = [effect_text(e, v) for e, v in board["effects"].items() if v]
    embed.add_field(
        name="📈 현재 효과",
        value="\n".join(effects) if effects else "아직 찍은 패시브가 없습니다.",
        inline=False,
    )
    return embed


# ------------------------------------------------------------------ 제작


def ingredient_text(spec: dict) -> str:
    """{'crop_type': '과일', 'min_stars': 3, 'count': 4} → '★3 이상 과일'"""
    if spec.get("crop"):
        name = spec["crop"]
    elif spec.get("crop_type"):
        name = spec["crop_type"]
    else:
        name = "아무 작물"
    if spec.get("variant") == "mutant":
        name = f"🧬돌연변이 {name}"
    elif spec.get("variant") == "golden":
        name = f"✨황금 {name}"
    if spec.get("min_stars"):
        name = f"★{spec['min_stars']} 이상 {name}"
    return name


def recipe_line(status: CraftRecipeStatus, selected: bool = False) -> str:
    recipe = status["recipe"]
    name = item_display_name(recipe["output"]["item"])
    if status["locked"]:
        state = f"🔒 Lv.{recipe['min_level']}"
    elif status["max_crafts"]:
        state = f"{status['max_crafts']}회 가능"
    else:
        state = "재료 부족"
    if selected:
        return f"▶ **{name}** — {state}"
    return f"• {name} — {state}"


def embed_craft(
    board: CraftBoard, user_name: str, selected: str | None = None
) -> discord.Embed:
    embed = discord.Embed(title="🔨 제작", color=discord.Color.orange())
    embed.description = user_header(user_name, board["level"], board["gold"])
    if board["material_pct"]:
        embed.description += f"\n🔧 패시브 재료 감소 **{board['material_pct']:g}%** 적용 중"

    # 제작 가능 / 재료 부족 / 잠김 으로 나눠서 보여 준다 (잠긴 레시피는 한 줄로 요약)
    ready = [s for s in board["recipes"] if not s["locked"] and s["max_crafts"]]
    short = [s for s in board["recipes"] if not s["locked"] and not s["max_crafts"]]
    locked = [s for s in board["recipes"] if s["locked"]]
    if not board["recipes"]:
        embed.add_field(name="📜 레시피", value="레시피가 없습니다.", inline=False)
    for title, group in ((f"✅ 제작 가능 ({len(ready)})", ready),
                         (f"📦 재료 부족 ({len(short)})", short)):
        if group:
            embed.add_field(
                name=title,
                value="\n".join(recipe_line(s, s["recipe"]["id"] == selected) for s in group),
                inline=False,
            )
    if locked:
        parts = []
        for s in sorted(locked, key=lambda s: s["recipe"]["min_level"]):
            name = item_display_name(s["recipe"]["output"]["item"])
            text = f"{name} Lv.{s['recipe']['min_level']}"
            parts.append(f"**▶ {text}**" if s["recipe"]["id"] == selected else text)
        embed.add_field(
            name=f"🔒 잠김 ({len(locked)})", value="-# " + " · ".join(parts), inline=False
        )

    status = next((s for s in board["recipes"] if s["recipe"]["id"] == selected), None)
    if status:
        recipe = status["recipe"]
        output = recipe["output"]
        lines = []
        for ing in status["ingredients"]:
            ok = "✅" if ing["have"] >= ing["need"] else "❌"
            lines.append(
                f"{ok} {ingredient_text(ing['spec'])} x{ing['need']} (보유 {ing['have']})"
            )
        if status["gold"]:
            ok = "✅" if board["gold"] >= status["gold"] else "❌"
            lines.append(f"{ok} 💰 {won(status['gold'])}")
        lines.append(f"➡️ {item_display_name(output['item'])} x{output['count']}")
        if status["locked"]:
            lines.append(f"🔒 농장 레벨 {recipe['min_level']} 이상 필요")
        embed.add_field(
            name=f"🧾 {recipe['name']}", value="\n".join(lines), inline=False
        )

    embed.set_footer(
        text="돌연변이·황금 작물은 그 조건이 있는 재료에만 쓰입니다. 판매가가 낮은 작물부터 소모됩니다."
    )
    return embed


# ------------------------------------------------------------------ 도감

CODEX_PAGE_SIZE = 15
CODEX_TITLES = {
    "normal": ("📖 작물 도감 · 일반", "일반"),
    "golden": ("✨ 작물 도감 · 황금", "황금"),
    "mutant": ("🧬 작물 도감 · 돌연변이", "돌연변이"),
}
CODEX_COLORS = {"normal": discord.Color.green(), "golden": discord.Color.gold(),
                "mutant": discord.Color.purple()}


def codex_total_pages(board: CodexBoard) -> int:
    return max(1, -(-len(board["entries"]) // CODEX_PAGE_SIZE))


def embed_codex(board: CodexBoard, user_name: str, page: int = 1) -> discord.Embed:
    variant = board["variant"]
    title, _ = CODEX_TITLES[variant]
    total_pages = codex_total_pages(board)
    page = min(max(page, 1), total_pages)

    embed = discord.Embed(title=title, color=CODEX_COLORS[variant])
    percent = board["discovered"] * 100 / board["total"] if board["total"] else 0
    summary = " · ".join(
        f"{CODEX_TITLES[v][1]} {found}/{total}" for v, (found, total) in board["progress"].items()
    )
    embed.description = (
        f"{user_header(user_name)}\n"
        f"📊 발견 **{board['discovered']}/{board['total']}** "
        f"`{progress_bar(board['discovered'], board['total'])}` {percent:.0f}%\n"
        f"-# {summary}"
    )

    # 이 페이지의 작물을 등급별로 묶는다. 미발견 작물은 이름을 가리고 개수만 보여 준다.
    start = (page - 1) * CODEX_PAGE_SIZE
    groups: dict[int, list[str]] = {}
    hidden: dict[int, int] = {}
    for entry in board["entries"][start : start + CODEX_PAGE_SIZE]:
        grade = CROPS_DATA[entry["crop"]]["buy_grade"]
        groups.setdefault(grade, [])
        if entry["discovered"]:
            groups[grade].append(
                f"✅ **{entry['crop']}** {stars_text(entry['best_stars'])}"
                f" · {entry['harvest_count']:,}회"
            )
        else:
            hidden[grade] = hidden.get(grade, 0) + 1
    for grade, lines in groups.items():
        if hidden.get(grade):
            lines.append(f"❔ 미발견 {hidden[grade]}종")
        embed.add_field(name=f"〔{grade_name(grade)}〕", value="\n".join(lines), inline=False)

    milestones = board.get("milestones", [])
    if milestones:
        embed.add_field(
            name=f"🎁 {CODEX_TITLES[variant][1]} 도감 보상",
            value=codex_reward_text(milestones, board["discovered"]),
            inline=False,
        )
    active = [effect_text(e, v) for e, v in board.get("effects", {}).items() if v]
    embed.add_field(
        name="📈 적용 중인 도감 효과 (전체 합계)",
        value=" · ".join(active) if active else "아직 달성한 도감 보상이 없습니다.",
        inline=False,
    )
    footer = f"페이지 {page}/{total_pages}"
    if variant != "normal":
        footer += " · 같은 작물이라도 일반·황금·돌연변이는 각각 따로 등록됩니다."
    embed.set_footer(text=footer)
    return embed


def codex_reward_text(milestones: list[dict], discovered: int) -> str:
    """달성한 보상은 ✅ 로, 다음 목표 하나만 진행 막대와 함께, 나머지는 개수만"""
    lines = []
    upcoming = [m for m in milestones if not m["achieved"]]
    for m in milestones:
        if m["achieved"]:
            lines.append(f"✅ {m['count']}종 — {effect_text(m['effect'], m['value'])}")
    if upcoming:
        goal = upcoming[0]
        lines.append(
            f"🎯 **{goal['count']}종** — {effect_text(goal['effect'], goal['value'])}\n"
            f"　`{progress_bar(discovered, goal['count'])}` {discovered}/{goal['count']}"
        )
        if len(upcoming) > 1:
            lines.append(f"-# 🔒 이후 보상 {len(upcoming) - 1}단계 더")
    else:
        lines.append("🏆 모든 보상을 달성했습니다!")
    return "\n".join(lines)
