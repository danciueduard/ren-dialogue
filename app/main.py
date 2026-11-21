"""Loopback API and local game mailbox for Ren Dialogue."""
import asyncio
from contextlib import asynccontextmanager, suppress
import logging
from pathlib import Path
import secrets

from fastapi import FastAPI, HTTPException, Request, Response
import httpx

from app.ren import MODEL_CONTEXT, DialogueError, GameState, RenDialogue, Turn, initialize_ren_database
from app.ren_bridge import Mailbox
from app.ren_config import ren_env
from app.ren_storage import database_snapshot, snapshot_etag


DATABASE = Path(__file__).resolve().parent.parent / "database" / "rin.db"
log = logging.getLogger(__name__)


async def warm_model(client, url, model):
    try:
        response = await client.post(url, json={
            "model": model, "messages": [], "stream": False, "keep_alive": "10m",
            "options": {"num_ctx": MODEL_CONTEXT},
        })
        response.raise_for_status()
    except httpx.HTTPError as error:
        log.warning("Could not warm the Ren model: %s", error)


def create_app(database=DATABASE, lua_dir=None, *, use_mailbox=True, transport=None, api_token=None,
               ollama_url=None, ollama_model=None):
    @asynccontextmanager
    async def lifespan(application):
        initialize_ren_database(database)
        url = ollama_url or ren_env("OLLAMA_URL", "http://127.0.0.1:11434/api/chat")
        model = ollama_model or ren_env("MODEL", "qwen3.5:4b")
        application.state.api_token = api_token if api_token is not None else ren_env("API_TOKEN", "")
        async with httpx.AsyncClient(timeout=55, transport=transport, trust_env=False) as client:
            application.state.engine = RenDialogue(database, client, url, model)
            tasks = []
            if transport is None and ren_env("WARMUP", "1") != "0":
                tasks.append(asyncio.create_task(warm_model(client, url, model)))
            if use_mailbox:
                directory = lua_dir or Path(ren_env("LUA_DIR", str(Path.home() / "Zomboid" / "Lua")))
                directory.mkdir(parents=True, exist_ok=True)
                tasks.append(asyncio.create_task(Mailbox(directory, application.state.engine).run()))
            try:
                yield
            finally:
                for task in tasks:
                    task.cancel()
                for task in tasks:
                    with suppress(asyncio.CancelledError):
                        await task

    application = FastAPI(title="Ren Dialogue", lifespan=lifespan)

    @application.get("/")
    async def status(request: Request):
        return {"character": "Rin", "model": request.app.state.engine.model}

    def authorize(request):
        token = request.app.state.api_token
        supplied = request.headers.get("authorization", "").encode("utf-8")
        if token and not secrets.compare_digest(supplied, f"Bearer {token}".encode("utf-8")):
            raise HTTPException(status_code=401, detail="The Ren Dialogue backend access key is incorrect.")

    @application.get("/connection")
    async def connection(request: Request):
        authorize(request)
        return {"character": "Rin", "protocol": 1}

    @application.get("/database")
    def sync_database(request: Request):
        if use_mailbox or not request.app.state.api_token:
            raise HTTPException(status_code=404)
        authorize(request)
        data = database_snapshot(database)
        etag = snapshot_etag(data)
        headers = {"ETag": etag, "Cache-Control": "private, no-store"}
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=headers)
        return Response(data, media_type="application/vnd.sqlite3", headers=headers)

    @application.post("/chat")
    async def chat(turn: Turn, request: Request):
        authorize(request)
        try:
            return {"reply": await request.app.state.engine.respond(turn)}
        except DialogueError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error

    @application.put("/state", status_code=204)
    async def sync_game_state(state: GameState, request: Request):
        authorize(request)
        try:
            await request.app.state.engine.sync_state(state)
        except DialogueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    return application


app = create_app()
