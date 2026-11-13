# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

A Korean-language Discord farming game bot built with discord.py (slash commands only) and PostgreSQL via asyncpg. User-facing strings, command names, and many parameter names are in Korean (e.g. `/농사`, `/회원가입`, parameter `작물이름`). Keep new user-facing text in Korean.

## Running

```bash
pip install -r requirements.txt   # lists only discord.py; asyncpg must also be installed
python main.py                    # start the bot (run from repo root: paths like ./cogs and data/*.json are relative)
python db_init.py                 # create tables/trigger only (main.py also does this on connect)
```

`config.py` is gitignored and must define `TOKEN`, `PG_HOST`, `PG_PORT`, `PG_USER`, `PG_PASSWORD`, `PG_DATABASE`.

Tests live in `test.py` (stdlib `unittest`, no pytest). Use the project venv (`.venv`); the system `C:\Python314` has no packages and isn't writable without admin.

```bash
.venv/Scripts/python.exe -m unittest test -v                                  # unit tests (mocked DB/Discord)
.venv/Scripts/python.exe -m unittest test.FarmServiceTest.test_plant_seed_success   # single test
FARMBOT_DB_TEST=1 .venv/Scripts/python.exe -m unittest test.DatabaseIntegrationTest  # real Postgres from config.py, rolled back
```

Services are mocked by patching `service.base.db` with a fake that has `connection()`/`transaction()` context managers, plus the repo classes in the service module's namespace (e.g. `service.farm.FarmRepository`). `discord.ui.View` needs a running event loop, so view tests use `IsolatedAsyncioTestCase`. Tests marked `@unittest.expectedFailure` record known bugs. Remove the marker when you fix one. There is no linter config or build step.

## Architecture

Layered: **cogs → service → repository → asyncpg**, with `ui/` for embeds and views.

- `main.py` — `FarmBot` auto-loads every `cogs/*.py` (not starting with `_`) as an extension, then copies global commands to a single hardcoded guild and syncs there (guild ID and `owner_ids` are hardcoded). The global `bot.tree.error` handler only gives a specific message for `UserNotRegistered`; everything else gets a generic error reply.
- `database/connection.py` — a module-level singleton `db = Database()` holds the asyncpg pool. Schema (`users`, `user_farms`, `inventory`) is created here with `CREATE TABLE IF NOT EXISTS` in `init_tables()`; there are no migrations, so schema changes go into that SQL. `IF NOT EXISTS` never alters an existing table, so a change to an existing column also needs an idempotent `ALTER TABLE` there (as done for the `BIGINT` user_id fix). Discord IDs exceed int32, so ID columns must be `BIGINT`. A Postgres trigger inserts farm slot 1 for every new user. `db.connection()` / `db.transaction()` are async context managers yielding a connection.
- `database/*_repo.py` — repositories subclass `BaseRepository` and take a connection in the constructor; they contain raw SQL only.
- `service/*.py` — services subclass `BaseService` (which exposes `self.db`), open a connection/transaction, and instantiate repositories on that connection. Multi-table operations (planting consumes a seed, harvesting grants item + exp) share one `db.transaction()` by constructing several repos on the same `conn`.
- `cogs/` — slash commands; instantiate services per call (e.g. `FarmService()`), stateless.
- `ui/views.py` — `FarmView(farm, author, page, current)` paginated farm UI (10 slots per page). It copies rows into dicts, because asyncpg Records are immutable. `current` is the selected `slot_id`, and the action buttons are enabled based on that slot's state. Callbacks never mutate state in place: after a service call they re-fetch with `get_farm` and edit the message with a fresh `FarmView` (`refresh()`). Picking a slot also builds a fresh view.
- `utils/errorcheck.py` — `app_commands.check` decorators: `is_registered()`, `is_target_registered(param_name)` (checks the user passed in a command parameter, looked up via `interaction.namespace`), `is_owner()`. Custom exceptions live in `exceptions.py`.
- `consistances.py` — loads `data/crops.json` once at import into `CROPS_DATA` (keyed by Korean crop name, schema = `CropData` in `utils/datatype.py`). `service/crop.get_crop_data()` reads from it. It also loads `data/main_quest.json` → `MAIN_QUESTS` (ordered list) and `data/quests.json` → `QUEST_POOL` (`{"daily": [...], "weekly": [...]}`), plus `QUEST_INDEX[type][id]`. Quest schema is `QuestData` in `utils/datatype.py`. `QuestDataTest` validates every quest's objective, targets and reward items against `crops.json`.
- `database/connection.py` keeps the whole schema in the module constant `SCHEMA_SQL` (idempotent). `init_tables()` runs it, and the integration tests run it inside their rolled-back transaction when a table is missing.

## Domain conventions

- Farm land grows with level: `data/farm.json` `slots_by_level` (`{min_level, slots}` rows, the last one with `min_level <= level` wins). `service.farm.slots_for_level`/`next_slot_level` read it. `FarmService.get_farm` calls `FarmRepository.ensure_slots(user, n)` (idempotent `INSERT … generate_series … ON CONFLICT DO NOTHING`) before reading, so land appears however the level was gained and existing users are backfilled. `level_up_text(level, level_before)` mentions newly opened land.
- Slot state is derived, not stored: no `user_farms` row → `SlotStatus.LOCKED`, row with `crop_id IS NULL` → `EMPTY`, otherwise `PLANTED`.
- `user_farms.planted_at` stores the **ready-at** Unix timestamp (`time() + grow_time`), not the planting time. Harvest succeeds only when `planted_at <= now`; the UI renders it as `<t:ts:R>`.
- Seeds are inventory items with id `<crop_name>_seed`; harvested crops are stored under the crop name itself.
- `InventoryRepository.update_inventory` is an upsert taking a signed delta. If the delta would drop the amount below 0 it **silently does nothing**: no exception, no return value. A negative delta on a missing row inserts the item with amount 0. Callers that consume items have to check the balance themselves (e.g. with `get_item`). `FarmService.plant_seed` checks the seed balance and that the slot is empty before planting.
- Services catch exceptions, print a traceback and return `False`/`None` instead of raising. Cogs and views branch on that return value, so errors from service code usually never reach `bot.tree.error`.
- `consistances.CROP_CHOICES` (`app_commands.Choice` list built from `crops.json`) is the source for crop-name parameter choices. The plant button in `FarmView` opens an ephemeral `SeedSelectView` listing owned seeds (`FarmService.get_seeds`). Picking one plants it and edits the original farm message.
- Level is computed from `farm_exp` in `UserService.calculate_level` (`floor(sqrt(exp/100)) + 1`) and is not stored in the DB.
- Quests (`service/quest.py`, `/퀘스트` → `QuestView` with 메인/일일/주간 tab buttons and one "보상 받기" button that claims everything completed on the current tab):
  - Progress lives in `user_quests (user_id, quest_type, quest_id, period, progress, claimed)`. `period` is `''` for main, `YYYY-MM-DD` for daily and `YYYY-Www` for weekly, all in KST (fixed UTC+9 offset, because Windows lacks tzdata for `zoneinfo`). Daily resets at 00:00, weekly on Monday at 00:00.
  - Main quests form a chain: the current one is the first id not yet claimed. It is locked while `min_level > level`, and only the current main quest gains progress.
  - Daily/weekly quests are picked from the pool filtered by `min_level`/`max_level` at the user's level. They are seeded by `(user, type, period)`, so the picks are deterministic, and they're stored when first needed. Rows from older periods are deleted on re-assign.
  - Objective types are `plant`/`harvest`/`water`, counted via `record_quest_progress(conn, user_id, event, crop)`, and `level`, which is computed and never stored. An objective can narrow down to one crop (`target`) or a crop category (`crop_type`, a `CropType` name such as `"과일"`), but not both. `objective_matches` checks whichever is set. Quest rewards can be seeds or fertilizers. `QuestDataTest.test_crop_objectives_are_achievable` requires a matching seed grade to be buyable in the shop at the quest's `min_level`, so gate fruit quests (all grade ≥3) at level 3+. `FarmService` calls `record_quest_progress` inside the same transaction as the action. Any new countable action should do the same.
  - `water_seed` only applies (and counts) when the slot has a crop that isn't watered yet.
  - Claiming uses `mark_claimed` (upsert … `WHERE NOT claimed RETURNING`) as the guard against double rewards.
  - asyncpg can't infer the types of bare `$n` inside `LEAST`/`GREATEST`, so cast them (`$5::int`).
- Transient result messages (seeds/rewards received, harvest result, admin item grant) disappear after `NOTICE_DELETE_AFTER` (10s). Use `ui.notice.send_notice(interaction, msg)`: it picks `response.send_message(delete_after=)` or `followup.send(wait=True)` + `message.delete(delay=)`, because `followup.send` has no `delete_after`. For `edit_message` pass `delete_after=NOTICE_DELETE_AFTER` directly. The main `/농사` and `/퀘스트` screens are not auto-deleted. In tests, `make_interaction()` tracks `response.is_done()` and `assert_notice()` checks the followup path.
- Shop (`service/shop.py`, `/상점` → `ShopView`: item select plus 1개/5개/최대 buttons):
  - Each user has their own shop in `user_shop (user_id, slot, period, item_id, price, stock, bought)`. It restocks every `refresh_hours` (3h, aligned to 00:00 KST, period key `YYYY-MM-DDTHH`) the first time it's opened in a new period. Price is stored at restock time.
  - `generate_stock` fills `slots` (seed 4, fertilizer 2). For each slot it picks a grade with `grade_weights[category]`: the last table row whose `min_level <= level`, with a weight of 0 meaning the grade never appears. Then it picks an unused item of that grade. It's seeded by `(user, period)`. A slot is skipped once the allowed grades run out of items: at level 1 only 3 seed kinds are eligible.
  - Grades are 1–5 (`GRADE_NAMES` 최하급…최상급). For seeds the grade is `crops.json` `buy_grade`, and for fertilizers it's `fertilizers.json` `grade`. All tuning lives in `data/shop.json`. `ShopDataTest` and `ShopOddsTest` assert that every grade has items and that better grades never get less likely as level rises.
  - `buy` decrements stock (`stock - bought >= n`), then calls `UserRepository.spend_gold` (`gold >= cost`). If gold is short it raises `NotEnoughGold` inside the transaction so the stock change rolls back.
- Fertilizer (`data/fertilizers.json`, item ids `<등급>_비료`): `FarmService.apply_fertilizer` reduces the remaining grow time of one slot by `reduce_minutes`, but never earlier than now. It works only while the crop is growing, and only once per planting, tracked by `user_farms.fertilizer`, which plant/harvest reset to NULL. The fertilizer is consumed only if the update applied. In the UI, `FarmView` has a "비료 주기" button that opens `FertilizerSelectView`.
- Event crops have `buy_grade: 0` (`EVENT_GRADE`). They never appear in the shop (`items_by_grade` skips them), so their seeds must come from quest rewards or recipes; `CropDataTest.test_event_crops_are_obtainable` enforces that. They may use any `crop_type`, and `difficulty: 0` means "없음" with no quality penalty. Never index `GRADE_NAMES` or `difficulty_*` lists directly. Use `consistances.grade_name` / `difficulty_name` / `difficulty_penalty` / `is_event_crop`.
- `crops.json` has 100+ crops spread over grades 1–5. Non-event crops must use a `utils.dataenum.CropType` member name as `crop_type`. `CropDataTest` checks that, that sell > buy, and that grow time, price and exp rise with grade on average. Discord select menus are limited to 25 options, so any list of crops/items shown in a select must paginate (see `SeedSelectView`, 25 per page with ◀/▶ buttons).
- Harvest quality (`service/produce.py`, tuning in `data/harvest.json`):
  - Each harvest rolls stars (1–5) and a mutation. `FarmRepository.harvest_crop` returns the pre-harvest row (`crop_id, planted_at, is_watered, fertilizer`) so `roll_quality` can score it.
  - `care_points` adds up: watering, fertilizer grade plus its `quality_bonus` (the 황금 비료 line), harvest timing (within `fresh_ratio`×grow_time of ready = bonus, beyond `late_ratio` = penalty), and the crop `difficulty` (1–3 in `crops.json`) as a penalty. The points pick a row of `star_weights`.
  - Produce is stored in inventory as `<crop>|<stars>` or `<crop>|<stars>|M` for a mutant. A bare crop name is the legacy format, read as ★1. Always go through `make_produce_id`/`parse_produce_id`. The sell price is `sell_price × star multiplier (× mutation multiplier)`, computed server-side by `produce_price`.
  - `harvest_crop` returns a `HarvestResult` dict, or `None` when there's nothing ready.
- Rot: if a crop is harvested `harvest.json` `rot.after_minutes_by_grade[buy_grade-1]` or more after it's ready (12h/6h/3h/1h/30m for 최하급…최상급, event crops use `event_after_minutes`), multiplied by `rot.difficulty_multipliers[difficulty-1]` (쉬움 1 / 보통 0.75 / 어려움 0.5; difficulty 0 = ×1), `harvest_crop` gives one `ROTTEN_ITEM` (`썩은_식물`) instead, with `rotten=True`, `stars=0` and no exp, quest progress or codex entry. Use `service.produce.rot_at`/`is_rotten`. The rotten item isn't produce for `parse_produce_id` (so crafting/codex ignore it), but `produce_price` gives it the fixed `rot.sell_price` and the warehouse lists it with the produce so it can be sold.
- Fertilizers come in two families, each with grades 1–5: speed (`<등급>_비료`, `reduce_minutes`) and quality (`<등급>_황금_비료`, `reduce_minutes: 0`, `quality_bonus`). Only one fertilizer per planting. `fertilizer_effect_text` renders the effect.
- Quest objectives may also carry `min_stars` (harvest only). `record_quest_progress(..., stars=)` is passed the rolled stars.
- `/창고` (`service/warehouse.py`, `WarehouseView`) lists produce/seeds/fertilizers and sells produce: 1개, 그 품목 전부, or 수확물 모두. `InventoryRepository.remove_item` is the strict decrement that fails rather than going partial. Seeds and fertilizers can't be sold.
- Produce variants: besides mutant `|M`, there is a rarer golden variant `|G`, priced by `harvest.json` `golden.price_multiplier`. `parse_produce_id` returns `(crop, stars, variant)` where variant is `None`, `"mutant"` or `"golden"`. A crop never gets both; `roll_quality` checks golden first, then mutant, and returns `(stars, variant)`.
- Passive tree (`service/passive.py`, `data/passives.json`, `/패시브` → `PassiveView`):
  - Players get `points_per_level` per level above 1. Ranks live in `user_passives`. A node unlocks when its `requires` ranks are met. Reset costs `level × reset_cost_per_level` gold and the UI asks for confirmation first.
  - `compute_effects(ranks)` returns the summed effect values, capped by `EFFECT_LIMITS`. Other services read them inside their own transaction with `get_effects(conn, user_id)`: harvest (`care_points`, `mutation_chance`, `golden_chance`), warehouse (`sell_price_pct`), shop (`shop_discount_pct`, applied to displayed and charged price via `apply_discount`), crafting (`craft_material_pct`, `craft_gold_pct`).
  - `invest` and `reset` lock the user row (`PassiveRepository.lock_user`, `FOR UPDATE`) so concurrent clicks can't overspend points.
  - In unit tests, wrap service tests in `no_passives(ranks)`, which patches `service.passive.PassiveRepository`.
- Crafting (`service/craft.py`, `data/recipes.json`, `/제작` → `CraftView`):
  - An ingredient spec can filter by `crop`, `crop_type`, `min_stars` and `variant`. A spec without `variant` never consumes mutant or golden produce. Matching produce is consumed cheapest-sell-price first.
  - The material passive applies `reduced_count`, which rounds and never goes below 1.
  - `craft` plans everything first, then spends gold and removes items inside one transaction. A failure raises `CraftFailed` so the whole craft rolls back.
- Harvest exp scales with stars: `service.produce.star_exp(base, stars)` uses `harvest.json` `star_exp_multipliers` (★1 ×1 … ★5 ×2.5), then `bonus_exp` applies the `exp_pct` buff. `HarvestResult.exp` holds the gained exp (0 when rotten), and the harvest notice shows it. Crop/shop displays show the ★1~★5 range via `ui.embed.exp_range_text`.
- `HarvestResult` includes `variant`, `golden` and `level_up` (the new level or None). Harvest and quest-claim notices tell the player to use `/패시브` after a level-up.
- Embed display conventions (`ui/embed.py`): write money as `won(n)` (`1,234원`, never `f"{n}원"`). Start every screen's description with `user_header(name, level, gold)`, and show level progress with `level_progress(exp, level)` (it uses `UserService.level_exp_range`). Lists start with a status icon so they scan quickly. Long lists group by state or grade and summarize what's hidden (locked recipes go on one line, undiscovered codex crops become a count). Page numbers go in the footer. The farm screen uses `slot_state`/`slot_line`/`slot_detail` in `ui/views.py`.
- Clickable command mentions: `utils.command_mention.command_mention("퀘스트")` returns `</퀘스트:ID>`. The IDs come from `tree.sync()` and are stored by `register_commands(synced)` in `main.py`. Before sync it falls back to `` `/퀘스트` ``. All user-facing text that points to another command uses it, including `UserNotRegistered`'s default message. Don't hard-code `` `/명령어` `` strings.
- `/게임창` (`cogs/game.py`) opens `GameHomeView`. Every main screen view (Farm/Shop/Craft/Warehouse/Codex/Passive/Quest) calls `add_game_nav(self, "<key>")` right after `_build_components()`, which adds a `GameNavSelect` on row 4 (screens only use rows 0–2). Picking a screen calls `build_game_screen(key, author)` and edits the message, so the nav survives every refresh because each new view adds it again. A new main screen must be added to `GAME_SCREENS` and `build_game_screen` and must keep row 4 free.
- `/명령어` (`cogs/help.py`) lists the registered commands from `bot.tree`, grouped by `CATEGORIES` (anything unlisted goes under "기타"). It hides admin commands, which are detected by `is_owner()`'s predicate carrying `admin_only = True`. Give every new command a `description`, since a test asserts none show Discord's "…" placeholder, and add it to a category.
- Codex (`service/codex.py`, `/도감` → `CodexView` with 일반/황금/돌연변이 tabs and 15 crops per page):
  - `user_codex (user_id, crop_id, variant, harvest_count, best_stars, first_at)` is keyed per crop × variant, where variant is `normal`, `golden` or `mutant` (`codex_variant(None)` → `normal`).
  - `FarmService.harvest_crop` calls `CodexRepository.record` in the harvest transaction. It returns `RETURNING (xmax = 0)`, which is true on the first insert, and that becomes `HarvestResult.new_codex` for the "도감 신규 등록" notice.
  - Only harvests made after this feature was added are recorded. There's no backfill.
  - Event crops (`buy_grade: 0`) are not part of the codex: `service.codex.in_codex(crop)` excludes them from the list, totals and reward counts, and `harvest_crop` doesn't record them (`new_codex=False`).
  - Undiscovered crops are shown as `❔ ??? · <grade>`.
  - Codex rewards: `data/codex_rewards.json` maps each variant to milestones `{count, effect, value}`. `codex_effects(codex_counts(rows))` sums the achieved ones.
- `service.passive.get_effects(conn, user_id)` is the single source of a user's buffs: `merge_effects(passive, codex)` with `EFFECT_LIMITS` caps. Add new buff sources there, not in individual services.
  - Effects also include `grow_time_pct`, applied at planting via `reduced_grow_time` (min 1s), and `exp_pct`, applied to harvest exp via `bonus_exp`.
  - In unit tests, `no_passives(ranks, codex_rows)` patches both `PassiveRepository` and `CodexRepository` inside `service.passive`.
- Cog `upgrade` is a stub. `codex` (`/도감`) only shows a placeholder count against `NUM_OF_CROPS`.
- Identifier misspellings are real names in the code, so don't "fix" them in only one place: `consistances.py`, `slot_vaild`.
