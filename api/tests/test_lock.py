import pytest

from app.context import AppContext
from app.lock import AlreadyRunning, DataDirLock

from .conftest import make_settings


def test_a_second_lock_on_the_same_folder_is_refused(tmp_path):
    first = DataDirLock(str(tmp_path))
    first.acquire()
    try:
        with pytest.raises(AlreadyRunning):
            DataDirLock(str(tmp_path)).acquire()
    finally:
        first.release()
    # free again once the first one let go
    again = DataDirLock(str(tmp_path))
    again.acquire()
    again.release()


async def test_a_second_app_on_the_same_data_refuses_to_start(ctx, tmp_path, broker_server):
    _, server = broker_server
    with pytest.raises(AlreadyRunning):
        await AppContext(make_settings(tmp_path, server)).start()
