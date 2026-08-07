from contextlib import asynccontextmanager

import asyncpg
import config
from typing import Union


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
            await conn.execute("""
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
            """)

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
