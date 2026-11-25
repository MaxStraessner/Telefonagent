import logging
import sys

from pythonjsonlogger.json import JsonFormatter


class OAuthAccessLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) == 5:
            path = str(args[2])
            if "/calendar/oauth/" in path and "/callback?" in path:
                record.args = (args[0], args[1], path.partition("?")[0], args[3], args[4])
        return True


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
    logging.getLogger("uvicorn.access").addFilter(OAuthAccessLogFilter())

