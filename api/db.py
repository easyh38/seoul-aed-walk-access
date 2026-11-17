"""DB 연결. 접속 주소는 .env의 DATABASE_URL에서 읽는다."""

import os

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL이 없습니다. .env.example을 복사해 .env를 만들고 값을 채우세요."
    )

# pool_pre_ping: 오래 쉬던 연결이 끊겼으면 쓰기 전에 다시 연결한다
engine = create_engine(DATABASE_URL, pool_pre_ping=True)


def query(sql, **params):
    """SELECT 결과를 dict 목록으로 돌려준다.

    값은 반드시 :이름 자리표시로 넘긴다 (문자열 이어 붙이기 금지 → SQL 인젝션 방지).
    예: query("SELECT * FROM aed WHERE id = :id", id=1)
    """
    with engine.connect() as conn:
        return [dict(row._mapping) for row in conn.execute(text(sql), params)]
