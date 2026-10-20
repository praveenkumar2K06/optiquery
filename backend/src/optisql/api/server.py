"""CLI runner to launch the OptiQuery FastAPI Web Server."""
from __future__ import annotations

import argparse
import sys
import uvicorn


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="optisql-web",
        description="Launch the OptiQuery Compiler & Optimizer Web Server",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host interface to bind (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        "-p",
        type=int,
        default=8000,
        help="Port number to listen on (default: 8000)",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable auto-reload on code changes",
    )
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    print(f"Starting OptiQuery Web Server on http://{args.host}:{args.port}")
    print(f"Interactive workbench available at http://{args.host}:{args.port}")
    print(f"Interactive API docs (Swagger) at http://{args.host}:{args.port}/docs")

    uvicorn.run(
        "optisql.api.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )


if __name__ == "__main__":
    main()

