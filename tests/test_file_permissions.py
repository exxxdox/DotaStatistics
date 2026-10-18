from types import SimpleNamespace
from unittest.mock import Mock

import lib.file_permissions as file_permissions


def test_secure_permissions_uses_chmod_on_posix(monkeypatch) -> None:
    path = Mock()
    monkeypatch.setattr(
        file_permissions, "os", SimpleNamespace(name="posix", environ={})
    )

    file_permissions.secure_permissions(path, 0o600)

    path.chmod.assert_called_once_with(0o600)


def test_secure_permissions_allows_host_managed_bind_mount(monkeypatch) -> None:
    path = Mock()
    monkeypatch.setattr(
        file_permissions,
        "os",
        SimpleNamespace(name="posix", environ={"SKIP_DATA_CHMOD": "1"}),
    )

    file_permissions.secure_permissions(path, 0o600)

    path.chmod.assert_not_called()
