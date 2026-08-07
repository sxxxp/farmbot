import asyncio
from database.connection import Database


async def main():
    db = Database()

    await db.connect()
    print("✅ DB 연결 성공!")

    await db.init_tables()
    print("✅ 테이블 초기화 완료!")

    if hasattr(db, "close"):
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
