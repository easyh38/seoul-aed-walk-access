"""DB 연결. 접속 주소는 .env의 DATABASE_URL에서 읽는다."""

import os

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url

load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL이 없습니다. .env.example을 복사해 .env를 만들고 값을 채우세요."
    )


def build_database_url(database_url: str) -> URL:
    """실행 환경에 따라 DB 호스트와 포트를 설정한다."""

    url = make_url(database_url)

    updates = {}
    host = os.getenv("DB_HOST")
    port_value = os.getenv("DB_PORT")

    if host:
        updates["host"] = host

    if port_value:
        try:
            port = int(port_value)
        except ValueError as exc:
            raise RuntimeError("DB_PORT는 정수여야 합니다.") from exc

        if not 1 <= port <= 65535:
            raise RuntimeError("DB_PORT는 1~65535 사이여야 합니다.")

        updates["port"] = port

    return url.set(**updates) if updates else url


ENGINE_DATABASE_URL = build_database_url(DATABASE_URL)

# pool_pre_ping: 오래 쉬던 연결이 끊겼으면 쓰기 전에 다시 연결한다
engine = create_engine(ENGINE_DATABASE_URL, pool_pre_ping=True)


def query(sql, **params):
    """SELECT 결과를 dict 목록으로 돌려준다.

    값은 반드시 :이름 자리표시로 넘긴다.
    """
    with engine.connect() as conn:
        return [dict(row._mapping) for row in conn.execute(text(sql), params)]
