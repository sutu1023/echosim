"""材料原件的私有存储：流式写入、SHA-256、按标识定位与读取。

存储路径只由项目与材料标识派生，绝不拼接用户文件名；写入先在 `.tmp` 下生成
临时文件，校验通过后再原子移动到正式路径，失败时不留残片。
"""

from __future__ import annotations

import hashlib
import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

CHUNK_SIZE = 64 * 1024
TEMP_DIRNAME = ".tmp"
EXTENSIONS = {"pdf": "pdf", "markdown": "md", "text": "txt"}


class EmptyUpload(Exception):
    """上传内容为空。"""


class FileTooLarge(Exception):
    """上传内容超过单文件上限。"""


@dataclass(frozen=True)
class SpooledUpload:
    """已写入临时文件并计算哈希的上传内容。"""

    temp_path: Path
    size_bytes: int
    sha256: str


def extension_for(kind: str) -> str:
    try:
        return EXTENSIONS[kind]
    except KeyError:  # pragma: no cover - 调用方已限定类型
        raise ValueError(f"未知材料类型：{kind}") from None


def relative_path(project_id: uuid.UUID, document_id: uuid.UUID, kind: str) -> str:
    return f"{project_id}/{document_id}/original.{extension_for(kind)}"


def absolute_path(root: Path, relative: str) -> Path:
    """把相对路径解析到存储根之内；越界路径一律拒绝。"""
    root_resolved = root.resolve()
    candidate = (root_resolved / relative).resolve()
    if candidate != root_resolved and root_resolved not in candidate.parents:
        raise ValueError("存储路径越界")
    return candidate


async def spool_upload(
    root: Path, chunks: AsyncIterator[bytes], max_bytes: int
) -> SpooledUpload:
    """把上传流写入临时文件，超过上限立即中止。"""
    temp_dir = root / TEMP_DIRNAME
    temp_dir.mkdir(parents=True, exist_ok=True)
    temp_path = temp_dir / f"{uuid.uuid4().hex}.part"
    digest = hashlib.sha256()
    size = 0
    try:
        with temp_path.open("wb") as handle:
            async for chunk in chunks:
                if not chunk:
                    continue
                size += len(chunk)
                if size > max_bytes:
                    raise FileTooLarge
                digest.update(chunk)
                handle.write(chunk)
        if size == 0:
            raise EmptyUpload
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise
    return SpooledUpload(temp_path=temp_path, size_bytes=size, sha256=digest.hexdigest())


def commit_spool(root: Path, spooled: SpooledUpload, relative: str) -> Path:
    """把临时文件原子移动到正式路径。"""
    target = absolute_path(root, relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    os.replace(spooled.temp_path, target)
    return target


def discard_spool(spooled: SpooledUpload) -> None:
    spooled.temp_path.unlink(missing_ok=True)


def read_bytes(root: Path, relative: str) -> bytes:
    return absolute_path(root, relative).read_bytes()
