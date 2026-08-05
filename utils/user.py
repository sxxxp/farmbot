import math


def calculate_level(exp: int) -> int:
    if exp < 0:
        return 1
    return math.floor(math.sqrt(exp / 100)) + 1
