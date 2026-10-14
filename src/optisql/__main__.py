"""Allow ``python -m optisql`` to run the CLI."""
from .cli.main import main

if __name__ == "__main__":
    raise SystemExit(main())
