from discord import app_commands


class UserNotRegistered(app_commands.AppCommandError):
    """유저가 DB에 없을 때 발생시킬 커스텀 예외"""

    def __init__(
        self,
        user_id: int,
        message: str | None = None,
    ):
        self.message = (
            message or "가입되지 않은 유저입니다. `/회원가입`을 먼저 진행해 주세요."
        )
        self.user_id = user_id
        super().__init__(self.message)


class UserNotAdmin(app_commands.AppCommandError):
    """관리자 명령어를 실행시키려고 할때 예외"""

    def __init__(self):
        super().__init__("이 명령어는 관리자만 사용가능합니다.")
