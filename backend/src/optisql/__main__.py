"""Allow ``python -m optisql`` to run the OptiQuery Web Server."""
from .api.server import main

if __name__ == "__main__":
    main()
