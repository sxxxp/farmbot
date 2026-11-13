from database.connection import BaseRepository


class QuestRepository(BaseRepository):

    async def get_quests(self, user_id: int, quest_type: str, period: str):
        """해당 기간의 퀘스트 진행 목록"""
        return await self.conn.fetch(
            """
            SELECT quest_id, progress, claimed
            FROM user_quests
            WHERE user_id = $1 AND quest_type = $2 AND period = $3;
            """,
            user_id,
            quest_type,
            period,
        )

    async def get_claimed_ids(self, user_id: int, quest_type: str) -> set[str]:
        """보상을 받은 퀘스트 id 목록 (메인 퀘스트 진행 단계 계산용)"""
        rows = await self.conn.fetch(
            """
            SELECT quest_id FROM user_quests
            WHERE user_id = $1 AND quest_type = $2 AND claimed;
            """,
            user_id,
            quest_type,
        )
        return {row["quest_id"] for row in rows}

    async def assign_quests(
        self, user_id: int, quest_type: str, period: str, quest_ids: list[str]
    ):
        """기간 퀘스트 배정. 이전 기간 기록은 삭제"""
        await self.conn.execute(
            """
            DELETE FROM user_quests
            WHERE user_id = $1 AND quest_type = $2 AND period <> $3;
            """,
            user_id,
            quest_type,
            period,
        )
        await self.conn.executemany(
            """
            INSERT INTO user_quests (user_id, quest_type, quest_id, period)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT DO NOTHING;
            """,
            [(user_id, quest_type, quest_id, period) for quest_id in quest_ids],
        )

    async def add_progress(
        self,
        user_id: int,
        quest_type: str,
        quest_id: str,
        period: str,
        amount: int,
        cap: int,
    ):
        """진행도 증가 (목표치 cap 을 넘지 않음, 보상 받은 퀘스트는 무시)"""
        await self.conn.execute(
            """
            INSERT INTO user_quests (user_id, quest_type, quest_id, period, progress)
            VALUES ($1, $2, $3, $4, LEAST($5::int, $6::int))
            ON CONFLICT (user_id, quest_type, quest_id, period) DO UPDATE
            SET progress = LEAST(user_quests.progress + $5::int, $6::int)
            WHERE NOT user_quests.claimed;
            """,
            user_id,
            quest_type,
            quest_id,
            period,
            amount,
            cap,
        )

    async def mark_claimed(
        self, user_id: int, quest_type: str, quest_id: str, period: str, progress: int
    ) -> bool:
        """보상 수령 처리. 이미 받았으면 False (중복 수령 방지)"""
        row = await self.conn.fetchrow(
            """
            INSERT INTO user_quests
                (user_id, quest_type, quest_id, period, progress, claimed)
            VALUES ($1, $2, $3, $4, $5, TRUE)
            ON CONFLICT (user_id, quest_type, quest_id, period) DO UPDATE
            SET claimed = TRUE
            WHERE NOT user_quests.claimed
            RETURNING quest_id;
            """,
            user_id,
            quest_type,
            quest_id,
            period,
            progress,
        )
        return row is not None
