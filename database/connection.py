from contextlib import asynccontextmanager

import asyncpg
import config
from typing import Union


# 테이블/트리거 생성 SQL. 여러 번 실행해도 안전하다 (init_tables, 통합 테스트에서 사용)
SCHEMA_SQL = """
    CREATE TABLE IF NOT EXISTS users (
        user_id BIGINT PRIMARY KEY,
        gold INT DEFAULT 100,
        farm_exp INT DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS user_farms (
        user_id BIGINT,
        slot_id INT,
        crop_id TEXT,
        planted_at BIGINT,
        is_watered BOOLEAN DEFAULT FALSE,
        PRIMARY KEY (user_id, slot_id),
        FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS inventory (
        user_id BIGINT,
        item_id TEXT,
        amount INTEGER DEFAULT 0 CHECK (amount >= 0),
        PRIMARY KEY (user_id, item_id),
        FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE
    );

    -- 퀘스트 진행도. period: 메인 '' / 일일 '2026-11-12' / 주간 '2026-W46'
    -- 메인 퀘스트의 level 목표는 저장하지 않고 레벨로 계산한다.
    CREATE TABLE IF NOT EXISTS user_quests (
        user_id BIGINT,
        quest_type TEXT,
        quest_id TEXT,
        period TEXT DEFAULT '',
        progress INT DEFAULT 0,
        claimed BOOLEAN DEFAULT FALSE,
        PRIMARY KEY (user_id, quest_type, quest_id, period),
        FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE
    );

    -- 유저별 상점 진열 (입고 주기마다 교체). period: '2026-11-12T09' (KST 입고 시작 시각)
    CREATE TABLE IF NOT EXISTS user_shop (
        user_id BIGINT,
        slot INT,
        period TEXT,
        item_id TEXT,
        price INT,
        stock INT,
        bought INT DEFAULT 0 CHECK (bought >= 0 AND bought <= stock),
        PRIMARY KEY (user_id, slot),
        FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE
    );

    -- 작물 도감: 작물 x 변종(normal/golden/mutant) 별 첫 수확일 / 수확 횟수 / 최고 별
    CREATE TABLE IF NOT EXISTS user_codex (
        user_id BIGINT,
        crop_id TEXT,
        variant TEXT,
        harvest_count INT NOT NULL DEFAULT 0,
        best_stars INT NOT NULL DEFAULT 0,
        first_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (user_id, crop_id, variant),
        FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE
    );

    -- 패시브 트리: 노드별 투자 랭크
    CREATE TABLE IF NOT EXISTS user_passives (
        user_id BIGINT,
        node_id TEXT,
        rank INT NOT NULL DEFAULT 0 CHECK (rank >= 0),
        PRIMARY KEY (user_id, node_id),
        FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE
    );

    -- 이번 재배에 사용한 비료 (재배당 1회)
    ALTER TABLE user_farms ADD COLUMN IF NOT EXISTS fertilizer TEXT;

    -- 예전에 INTEGER 로 만들어진 테이블 보정 (디스코드 ID 는 int32 범위를 넘음).
    -- CREATE TABLE IF NOT EXISTS 는 기존 테이블을 바꾸지 않으므로 여기서 맞춘다.
    -- 이미 BIGINT 면 아무 변화 없음.
    ALTER TABLE users      ALTER COLUMN user_id    TYPE BIGINT;
    ALTER TABLE user_farms ALTER COLUMN user_id    TYPE BIGINT,
                           ALTER COLUMN planted_at TYPE BIGINT;
    ALTER TABLE inventory  ALTER COLUMN user_id    TYPE BIGINT;

    CREATE OR REPLACE FUNCTION create_initial_user_farm()
    RETURNS TRIGGER AS $$
    BEGIN
        INSERT INTO user_farms (user_id, slot_id)
        VALUES (
            NEW.user_id,
            1
        );

        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;

    CREATE OR REPLACE TRIGGER trigger_on_user_created
        AFTER INSERT ON users
        FOR EACH ROW
        EXECUTE FUNCTION create_initial_user_farm();
"""


class Database:
    def __init__(self):
        self.pool: asyncpg.Pool | None = None

    async def connect(self):
        """DB 커넥션 풀 생성 및 테이블 초기화"""
        self.pool = await asyncpg.create_pool(
            host=config.PG_HOST,
            port=config.PG_PORT,
            user=config.PG_USER,
            password=config.PG_PASSWORD,
            database=config.PG_DATABASE,
            min_size=5,
            max_size=20,
        )
        print("✅ PostgreSQL 커넥션 풀 생성 완료")
        await self.init_tables()

    async def close(self):
        """봇 종료 시 커넥션 풀 닫기"""
        if self.pool:
            await self.pool.close()

    async def init_tables(self):
        """기본 테이블 생성"""
        async with self.pool.acquire() as conn:
            await conn.execute(SCHEMA_SQL)

            return print("DB INIT 성공")
        print("DB INIT 실패")

    @asynccontextmanager
    async def connection(self):
        async with self.pool.acquire() as conn:
            yield conn

    @asynccontextmanager
    async def transaction(self):
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                yield conn


db = Database()


class BaseRepository:
    def __init__(self, conn: asyncpg.Connection):
        self.conn = conn
