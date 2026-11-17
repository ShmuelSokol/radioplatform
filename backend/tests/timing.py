"""Speed limits for tests. Shared CI runners are 2-3x slower and noisier than the
server, so a limit is tripled there; locally (and on the server) the real limit holds."""
import os


def limit(seconds: float) -> float:
    return seconds * 3 if os.environ.get("CI") else seconds
