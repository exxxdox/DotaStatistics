"""运行数据的 POSIX 权限收紧，允许宿主机文件系统接管权限。"""

import os
from pathlib import Path


def secure_permissions(path: Path, mode: int) -> None:
    """POSIX 卷收紧权限；不支持 chmod 的 bind mount 由宿主机 ACL 保护。"""
    if os.name == "posix" and os.environ.get("SKIP_DATA_CHMOD") != "1":
        path.chmod(mode)
