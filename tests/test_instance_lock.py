import pytest

from albridge.risk.instance_lock import (
    AlbridgeAlreadyRunning,
    single_instance_lock,
)


def test_second_instance_is_blocked(tmp_path):
    lock_path = tmp_path / "albridge.lock"

    with single_instance_lock(lock_path):
        with pytest.raises(AlbridgeAlreadyRunning):
            with single_instance_lock(lock_path):
                pass


def test_lock_is_released_after_exit(tmp_path):
    lock_path = tmp_path / "albridge.lock"

    with single_instance_lock(lock_path):
        pass

    # A new process could acquire it after the first exits.
    with single_instance_lock(lock_path):
        assert lock_path.exists()


def test_lock_is_released_after_exception(tmp_path):
    lock_path = tmp_path / "albridge.lock"

    with pytest.raises(RuntimeError, match="test failure"):
        with single_instance_lock(lock_path):
            raise RuntimeError("test failure")

    with single_instance_lock(lock_path):
        assert lock_path.exists()