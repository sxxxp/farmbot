import asyncpg
from database.connection import db


class BaseService:
    def __init__(self):
        self.db = db
