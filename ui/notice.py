import discord

# 씨앗/보상 획득 같은 알림 메시지가 자동으로 삭제되기까지의 시간(초)
NOTICE_DELETE_AFTER = 10


async def send_notice(
    interaction: discord.Interaction,
    content: str,
    *,
    ephemeral: bool = True,
    delete_after: float = NOTICE_DELETE_AFTER,
):
    """잠깐 보였다가 delete_after 초 뒤에 사라지는 알림 메시지.

    아직 응답하지 않은 interaction 이면 response 로, 이미 응답했으면 followup 으로 보낸다.
    (followup.send 에는 delete_after 옵션이 없어서 보낸 뒤 delete(delay=) 로 예약)
    """
    if not interaction.response.is_done():
        await interaction.response.send_message(
            content, ephemeral=ephemeral, delete_after=delete_after
        )
        return

    message = await interaction.followup.send(content, ephemeral=ephemeral, wait=True)
    await message.delete(delay=delete_after)
