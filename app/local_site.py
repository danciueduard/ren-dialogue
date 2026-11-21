"""Serve the FLDN browser build and the roleplay API on one local address."""

import argparse
from pathlib import Path

from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.routing import Mount
from starlette.staticfiles import StaticFiles

from app.main import app as engine, lifespan


class AngularSite(StaticFiles):
    async def get_response(self, path, scope):
        try:
            return await super().get_response(path, scope)
        except HTTPException as error:
            if error.status_code != 404 or Path(path).suffix:
                raise
            return await super().get_response("index.html", scope)


def create_site(frontend: Path):
    if not (frontend / "index.html").is_file():
        raise ValueError(f"Angular build not found at {frontend}")
    return Starlette(lifespan=lifespan, routes=[
        Mount("/roleplay-api", app=engine),
        Mount("/", app=AngularSite(directory=frontend, html=True)),
    ])


if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("frontend", type=Path, help="Path to the Angular browser output directory")
    parser.add_argument("--host", default="127.0.0.1", help="Use the PC's private IP for Wi-Fi access")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    uvicorn.run(create_site(args.frontend), host=args.host, port=args.port)
