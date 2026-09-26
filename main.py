import logging
import sys

from pushlog.bot import PushLogError, run


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    try:
        run()
    except PushLogError as exc:
        logging.error("%s", exc)
        sys.exit(1)
