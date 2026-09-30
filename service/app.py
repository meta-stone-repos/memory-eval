import hmac
import logging
import os
import time
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator

from backends.sqlite_bm25 import RequestConflict, SQLiteBM25


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Message(Contract):
    role: Literal['user', 'assistant']
    content: StrictStr = Field(min_length=1)
    timestamp: StrictInt | None = None

    @field_validator('content')
    @classmethod
    def not_blank(cls, value):
        if not value.strip():
            raise ValueError('content must not be blank')
        return value

    @field_validator('timestamp')
    @classmethod
    def valid_time(cls, value):
        if value is not None and not 0 <= value <= 253402300799999:
            raise ValueError('timestamp must be supported Unix milliseconds')
        return value


class Add(Contract):
    request_id: StrictStr = Field(min_length=1)
    user_id: StrictStr = Field(min_length=1)
    session_id: StrictStr = Field(min_length=1)
    messages: list[Message] = Field(min_length=1)


class Search(Contract):
    query: StrictStr = Field(min_length=1)
    user_id: StrictStr = Field(min_length=1)
    top_k: StrictInt = Field(ge=1)
    options: list[StrictStr] | None = None

    @field_validator('query')
    @classmethod
    def not_blank(cls, value):
        if not value.strip():
            raise ValueError('query must not be blank')
        return value


def create_app(db_path=None, api_key=None):
    app = FastAPI(title='AML-compatible text memory baseline', version='0.1.0')
    backend = SQLiteBM25(db_path or os.getenv('MEMORY_DB_PATH', '/home/xhy/data/memory-eval/memory.sqlite3'))
    key = os.getenv('MEMORY_API_KEY', '') if api_key is None else api_key
    logger = logging.getLogger('uvicorn.error')

    def authenticate(request: Request):
        if not key:
            return
        header = request.headers.get('authorization', '')
        scheme, _, credential = header.partition(' ')
        candidates = [request.headers.get('x-api-key', '')]
        if scheme.lower() in ('token', 'bearer'):
            candidates.append(credential)
        if not any(hmac.compare_digest(candidate, key) for candidate in candidates):
            raise HTTPException(401, 'Invalid memory service credential')

    @app.middleware('http')
    async def timing(request, call_next):
        start = time.monotonic()
        response = await call_next(request)
        logger.info('%s %s status=%s elapsed=%.3fs', request.method, request.url.path,
                    response.status_code, time.monotonic() - start)
        return response

    @app.get('/health')
    def health():
        with backend.connect() as db:
            db.execute('SELECT 1')
        return {'status': 'ok', 'backend': 'sqlite-bm25', 'tracks': ['textual', 'coding']}

    @app.post('/add', dependencies=[Depends(authenticate)])
    def add(payload: Add):
        try:
            backend.add(payload.model_dump(exclude_none=True))
        except RequestConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        return {'success': True, 'request_id': payload.request_id,
                'user_id': payload.user_id, 'session_id': payload.session_id}

    @app.post('/search', dependencies=[Depends(authenticate)])
    def search(payload: Search):
        return {'data': backend.search(payload.user_id, payload.query, payload.top_k)}

    return app


app = create_app()
