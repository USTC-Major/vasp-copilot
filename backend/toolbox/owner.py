"""OS-held process ownership. A crash releases the lock without deleting files."""
import os
from pathlib import Path

class OwnerBusy(RuntimeError):
    pass

class ProcessOwner:
    def __init__(self, root: Path, name: str = 'execution'):
        self.path = Path(root) / (name + '.owner.lock')
        self.handle = None

    def acquire(self):
        if self.handle is not None:
            return self
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open('a+b')
        if handle.seek(0, 2) == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise OwnerBusy(f'Another {self.path.stem} process owns this data directory; use one worker.') from exc
        self.handle = handle
        return self

    def close(self):
        handle, self.handle = self.handle, None
        if handle is None:
            return
        try:
            handle.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *args):
        self.close()
