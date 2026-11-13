"""슬래시 명령어 멘션 (`</이름:ID>`).

디스코드 채팅에서 `</퀘스트:123456789>` 처럼 쓰면 클릭할 수 있는 명령어로 표시된다.
명령어 ID 는 tree.sync() 결과에서만 알 수 있으므로 봇 시작 시 register_commands() 로 저장한다.
ID 를 모르면(동기화 전/실패) `/퀘스트` 텍스트로 대신 표시한다.
"""

from typing import Iterable

from discord import app_commands

_COMMAND_IDS: dict[str, int] = {}


def register_commands(commands: Iterable[app_commands.AppCommand]):
    """tree.sync() 가 돌려준 AppCommand 목록에서 이름 -> ID 를 저장"""
    for command in commands:
        _COMMAND_IDS[command.name] = command.id


def command_mention(name: str) -> str:
    command_id = _COMMAND_IDS.get(name)
    if command_id is None:
        return f"`/{name}`"
    return f"</{name}:{command_id}>"
