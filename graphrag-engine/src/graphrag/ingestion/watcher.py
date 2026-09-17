from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer


class _DirtyFlagHandler(FileSystemEventHandler):
    def __init__(self) -> None:
        self.dirty = False

    def on_any_event(self, event) -> None:
        if event.src_path.endswith(".md"):
            self.dirty = True


def watch(normalized_dir: Path, on_change: Callable[[], None], poll_interval: float = 2.0) -> None:
    """Chạy vô hạn, gọi on_change() khi có .md đổi trong normalized_dir (debounce theo poll_interval)."""
    handler = _DirtyFlagHandler()
    observer = Observer()
    observer.schedule(handler, str(normalized_dir), recursive=True)
    observer.start()
    try:
        while True:
            time.sleep(poll_interval)
            if handler.dirty:
                handler.dirty = False
                on_change()
    except KeyboardInterrupt:
        pass
    finally:
        observer.stop()
        observer.join()
