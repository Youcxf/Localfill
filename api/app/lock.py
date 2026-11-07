import os


class AlreadyRunning(RuntimeError):
    pass


# a lock on a file in the data folder, held while localfill runs. a second copy using the
# same data folder (and so the same tradovate logins) would place every order twice, so it
# refuses to start. the os drops the lock by itself if the process dies, so a crash never
# leaves a stale lock behind
class DataDirLock:
    def __init__(self, data_dir):
        self.path = os.path.join(data_dir, "localfill.lock")
        self.file = None

    def acquire(self):
        self.file = open(self.path, "a+")
        try:
            if os.name == "nt":
                import msvcrt

                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            self.file = None
            raise AlreadyRunning(
                f"another localfill is already running with the data folder {os.path.dirname(self.path)}. "
                "two copies would place every order twice"
            ) from None

    def release(self):
        if self.file is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        self.file.close()
        self.file = None
