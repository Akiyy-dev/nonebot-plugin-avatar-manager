import asyncio
import hashlib
import io
import json
import random
import shutil
from pathlib import Path
from urllib.parse import unquote, urlparse

import httpx
from nonebot import logger
from PIL import Image, UnidentifiedImageError

DATA_DIR = Path("data/avatar_manager")
RESOURCE_DIR = DATA_DIR / "resources"
STATE_FILE = DATA_DIR / "resource_state.json"
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
ALLOWED_IMAGE_FORMATS = {"JPEG", "PNG", "GIF", "WEBP", "BMP"}
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_TEXT_BYTES = 512 * 1024
MAX_MANIFEST_LINES = 500
MAX_MANIFEST_LINE_LENGTH = 2048
MAX_NAME_LENGTH = 128
MAX_IMAGE_WIDTH = 4096
MAX_IMAGE_HEIGHT = 4096
MAX_IMAGE_PIXELS = 4096 * 4096
AVATAR_LIST_PAGE_SIZE = 15
NAME_LIST_PAGE_SIZE = 20

_selection_state: dict[str, dict[str, list[str]]] | None = None


def build_target_key(target_type: str, target_id: int | None) -> str:
    if target_type == "group" and target_id is not None:
        return f"group_{target_id}"
    return "self"


def _ensure_target_paths(target_key: str) -> dict[str, Path]:
    base_dir = RESOURCE_DIR / target_key
    uploaded_avatar_dir = base_dir / "uploaded_avatars"
    remote_avatar_dir = base_dir / "remote_avatars"

    base_dir.mkdir(parents=True, exist_ok=True)
    uploaded_avatar_dir.mkdir(parents=True, exist_ok=True)
    remote_avatar_dir.mkdir(parents=True, exist_ok=True)

    return {
        "base": base_dir,
        "uploaded_avatar_dir": uploaded_avatar_dir,
        "remote_avatar_dir": remote_avatar_dir,
        "uploaded_avatar_list_file": base_dir / "avatar_storage_list.txt",
        "uploaded_names_file": base_dir / "uploaded_names.txt",
    }


def _load_selection_state() -> dict[str, dict[str, list[str]]]:
    global _selection_state
    if _selection_state is not None:
        return _selection_state

    if not STATE_FILE.exists():
        _selection_state = {}
        return _selection_state

    try:
        raw_state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exception:
        logger.warning(f"读取资源状态失败: {exception}")
        _selection_state = {}
        return _selection_state

    _selection_state = {
        str(key): {
            "avatar_history": list(value.get("avatar_history", [])),
            "name_history": list(value.get("name_history", [])),
        }
        for key, value in raw_state.items()
        if isinstance(value, dict)
    }
    return _selection_state


def _save_selection_state() -> None:
    state = _load_selection_state()
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as exception:
        logger.warning(f"保存资源状态失败: {exception}")


def _prune_selection_history(target_key: str, kind: str, removed_value: str) -> None:
    state = _load_selection_state()
    target_state = state.get(target_key)
    if target_state is None:
        return

    history_key = f"{kind}_history"
    history = target_state.get(history_key, [])
    filtered_history = [item for item in history if item != removed_value]
    if filtered_history == history:
        return

    target_state[history_key] = filtered_history
    _save_selection_state()


def _is_remote_url(value: str) -> bool:
    return value.startswith(("http://", "https://"))


def _ensure_remote_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("远程资源仅支持合法的 http/https 地址")


def _normalize_path_value(path: Path) -> str:
    return str(path.resolve())


def _path_suffix_from_url(url: str) -> str:
    return Path(urlparse(url).path).suffix.lower()


def _looks_like_txt_source(value: str) -> bool:
    if _is_remote_url(value):
        return _path_suffix_from_url(value) == ".txt"

    local_path = Path(value)
    return (
        local_path.exists()
        and local_path.is_file()
        and local_path.suffix.lower() == ".txt"
    )


def _looks_like_image_file_source(value: str) -> bool:
    if _is_remote_url(value):
        return _path_suffix_from_url(value) in IMAGE_SUFFIXES

    local_path = Path(value)
    return (
        local_path.exists()
        and local_path.is_file()
        and local_path.suffix.lower() in IMAGE_SUFFIXES
    )


def _looks_like_directory_source(value: str) -> bool:
    local_path = Path(value)
    return local_path.exists() and local_path.is_dir()


def _deduplicate_preserving_order(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _validate_manifest_lines(entries: list[str], *, kind: str) -> list[str]:
    if len(entries) > MAX_MANIFEST_LINES:
        raise ValueError(f"{kind}清单条目过多，最多允许 {MAX_MANIFEST_LINES} 行")

    validated_entries: list[str] = []
    for entry in entries:
        if len(entry) > MAX_MANIFEST_LINE_LENGTH:
            raise ValueError(f"{kind}清单中存在超长条目")
        validated_entries.append(entry)

    return validated_entries


def _validate_name_value(name: str) -> str:
    normalized_name = name.strip()
    if not normalized_name:
        raise ValueError("名称内容不能为空")
    if len(normalized_name) > MAX_NAME_LENGTH:
        raise ValueError(f"名称长度不能超过 {MAX_NAME_LENGTH} 个字符")
    return normalized_name


def _split_non_empty_lines(text: str) -> list[str]:
    return [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def _read_lines(path: Path) -> list[str]:
    if not path.exists():
        return []

    try:
        return _split_non_empty_lines(path.read_text(encoding="utf-8"))
    except OSError as exception:
        logger.warning(f"读取文本资源失败: {path} | error={exception}")
        return []


def _append_line(path: Path, value: str) -> None:
    existing_values = _read_lines(path)
    if value in existing_values:
        return

    try:
        with path.open("a", encoding="utf-8") as file:
            file.write(value + "\n")
    except OSError as exception:
        raise ValueError(f"保存存储列表失败: {exception}") from exception


def _rewrite_lines(path: Path, values: list[str]) -> None:
    try:
        path.write_text("\n".join(values), encoding="utf-8")
        if values:
            with path.open("a", encoding="utf-8") as file:
                file.write("\n")
    except OSError as exception:
        raise ValueError(f"更新存储列表失败: {exception}") from exception


def has_uploaded_avatars(target_type: str, target_id: int | None) -> bool:
    target_key = build_target_key(target_type, target_id)
    paths = _ensure_target_paths(target_key)
    return any(path.is_file() for path in paths["uploaded_avatar_dir"].iterdir())


def has_uploaded_names(target_type: str, target_id: int | None) -> bool:
    target_key = build_target_key(target_type, target_id)
    paths = _ensure_target_paths(target_key)
    return bool(_read_lines(paths["uploaded_names_file"]))


async def _download_bytes(url: str) -> bytes:
    _ensure_remote_url(url)
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(20.0, connect=10.0),
        follow_redirects=True,
    ) as client:
        async with client.stream("GET", url) as response:
            response.raise_for_status()

            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > MAX_IMAGE_BYTES:
                raise ValueError(f"远程图片大小超过限制 {MAX_IMAGE_BYTES} 字节")

            chunks: list[bytes] = []
            total_bytes = 0
            async for chunk in response.aiter_bytes():
                total_bytes += len(chunk)
                if total_bytes > MAX_IMAGE_BYTES:
                    raise ValueError(f"远程图片大小超过限制 {MAX_IMAGE_BYTES} 字节")
                chunks.append(chunk)

    return b"".join(chunks)


async def _read_remote_text(url: str) -> str:
    _ensure_remote_url(url)
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(20.0, connect=10.0),
        follow_redirects=True,
    ) as client:
        async with client.stream("GET", url) as response:
            response.raise_for_status()

            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > MAX_TEXT_BYTES:
                raise ValueError(f"文本清单大小超过限制 {MAX_TEXT_BYTES} 字节")

            chunks: list[bytes] = []
            total_bytes = 0
            async for chunk in response.aiter_bytes():
                total_bytes += len(chunk)
                if total_bytes > MAX_TEXT_BYTES:
                    raise ValueError(f"文本清单大小超过限制 {MAX_TEXT_BYTES} 字节")
                chunks.append(chunk)

    try:
        return b"".join(chunks).decode("utf-8")
    except UnicodeDecodeError as exception:
        raise ValueError("文本清单必须为 UTF-8 编码") from exception


def _safe_filename_from_url(url: str, suffix_fallback: str) -> str:
    parsed = urlparse(url)
    filename = Path(unquote(parsed.path)).name or "resource"
    suffix = Path(filename).suffix or suffix_fallback
    stem = Path(filename).stem or "resource"
    digest = hashlib.md5(url.encode("utf-8")).hexdigest()[:8]
    return f"{stem}_{digest}{suffix}"


async def _write_bytes(path: Path, content: bytes) -> Path:
    def _write() -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    return await asyncio.to_thread(_write)


def _validate_image_content(content: bytes) -> None:
    if len(content) > MAX_IMAGE_BYTES:
        raise ValueError(f"图片大小超过限制 {MAX_IMAGE_BYTES} 字节")

    try:
        with Image.open(io.BytesIO(content)) as image:
            image_format = (image.format or "").upper()
            width, height = image.size
            if image_format not in ALLOWED_IMAGE_FORMATS:
                raise ValueError("图片格式不受支持")
            if width > MAX_IMAGE_WIDTH or height > MAX_IMAGE_HEIGHT:
                raise ValueError("图片尺寸超过限制")
            if width * height > MAX_IMAGE_PIXELS:
                raise ValueError("图片像素总数超过限制")
            image.verify()
    except UnidentifiedImageError as exception:
        raise ValueError("资源内容不是有效图片") from exception
    except OSError as exception:
        raise ValueError("资源内容不是有效图片") from exception


async def _validate_local_image_file(path: Path) -> str:
    if not path.exists() or not path.is_file():
        raise ValueError("图片文件不存在")
    file_size = path.stat().st_size
    if file_size > MAX_IMAGE_BYTES:
        raise ValueError(f"图片大小超过限制 {MAX_IMAGE_BYTES} 字节")

    image_bytes = await asyncio.to_thread(path.read_bytes)
    _validate_image_content(image_bytes)
    return _normalize_path_value(path)


def _is_image_reference(value: str) -> bool:
    if _is_remote_url(value):
        return _path_suffix_from_url(value) in IMAGE_SUFFIXES

    local_path = Path(value)
    return (
        local_path.exists()
        and local_path.is_file()
        and local_path.suffix.lower() in IMAGE_SUFFIXES
    )


async def _read_text_source_lines(source: str) -> list[str]:
    if _is_remote_url(source):
        entries = _split_non_empty_lines(await _read_remote_text(source))
        return _validate_manifest_lines(entries, kind="文本")

    source_path = Path(source)
    if not source_path.exists() or not source_path.is_file():
        raise ValueError("文本清单不存在")

    try:
        text = await asyncio.to_thread(source_path.read_text, encoding="utf-8")
    except OSError as exception:
        raise ValueError(f"读取文本清单失败: {exception}") from exception

    entries = _split_non_empty_lines(text)
    return _validate_manifest_lines(entries, kind="文本")


async def classify_source_token(value: str) -> str | None:
    if _looks_like_image_file_source(value):
        return "avatar"

    if _looks_like_directory_source(value):
        return "avatar_collection"

    if not _looks_like_txt_source(value):
        return None

    entries = await _read_text_source_lines(value)
    if not entries:
        raise ValueError("文本清单为空")

    if all(_is_image_reference(entry) for entry in entries):
        return "avatar_manifest"

    return "name_manifest"


async def save_uploaded_image(
    target_type: str,
    target_id: int | None,
    image_source: str,
) -> Path:
    target_key = build_target_key(target_type, target_id)
    paths = _ensure_target_paths(target_key)
    timestamp = hashlib.md5(image_source.encode("utf-8")).hexdigest()[:8]

    if _is_remote_url(image_source):
        suffix = _path_suffix_from_url(image_source) or ".jpg"
        file_path = paths["uploaded_avatar_dir"] / f"upload_{timestamp}{suffix}"
        content = await _download_bytes(image_source)
        _validate_image_content(content)
        saved_path = await _write_bytes(file_path, content)
        _append_line(
            paths["uploaded_avatar_list_file"],
            _normalize_path_value(saved_path),
        )
        return saved_path

    source_path = Path(image_source)
    if not source_path.exists() or not source_path.is_file():
        raise ValueError("上传的图片资源无效")
    if source_path.stat().st_size > MAX_IMAGE_BYTES:
        raise ValueError(f"图片大小超过限制 {MAX_IMAGE_BYTES} 字节")

    source_bytes = await asyncio.to_thread(source_path.read_bytes)
    _validate_image_content(source_bytes)

    suffix = source_path.suffix or ".jpg"
    file_path = paths["uploaded_avatar_dir"] / f"upload_{timestamp}{suffix}"

    def _copy() -> Path:
        shutil.copy2(source_path, file_path)
        return file_path

    saved_path = await asyncio.to_thread(_copy)
    _append_line(
        paths["uploaded_avatar_list_file"],
        _normalize_path_value(saved_path),
    )
    return saved_path


def save_uploaded_name(target_type: str, target_id: int | None, name: str) -> bool:
    normalized_name = _validate_name_value(name)

    target_key = build_target_key(target_type, target_id)
    paths = _ensure_target_paths(target_key)
    existing_names = _read_lines(paths["uploaded_names_file"])
    if normalized_name in existing_names:
        return False

    _append_line(paths["uploaded_names_file"], normalized_name)

    return True


def _list_local_image_files(directory: Path) -> list[str]:
    return [
        _normalize_path_value(path)
        for path in sorted(directory.iterdir())
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
    ]


def _list_uploaded_images(target_key: str) -> list[str]:
    paths = _ensure_target_paths(target_key)
    listed_images = [
        image_path
        for image_path in _read_lines(paths["uploaded_avatar_list_file"])
        if Path(image_path).exists()
    ]
    directory_images = _list_local_image_files(paths["uploaded_avatar_dir"])
    return _deduplicate_preserving_order(listed_images + directory_images)


def _list_uploaded_names(target_key: str) -> list[str]:
    paths = _ensure_target_paths(target_key)
    return _read_lines(paths["uploaded_names_file"])


def get_local_storage_summary(
    target_type: str,
    target_id: int | None,
) -> dict[str, int]:
    target_key = build_target_key(target_type, target_id)
    return {
        "avatar_count": len(_list_uploaded_images(target_key)),
        "name_count": len(_list_uploaded_names(target_key)),
    }


def get_local_storage_page(
    target_type: str,
    target_id: int | None,
    kind: str,
    page: int,
) -> tuple[list[str], int, int, int]:
    if page < 1:
        raise ValueError("页码必须大于等于 1")

    target_key = build_target_key(target_type, target_id)
    if kind == "avatar":
        all_items = [Path(item).name for item in _list_uploaded_images(target_key)]
        page_size = AVATAR_LIST_PAGE_SIZE
    elif kind == "name":
        all_items = _list_uploaded_names(target_key)
        page_size = NAME_LIST_PAGE_SIZE
    else:
        raise ValueError("不支持的存储列表类型")

    total = len(all_items)
    total_pages = max((total + page_size - 1) // page_size, 1)
    if total and page > total_pages:
        raise ValueError(f"页码超出范围，当前最大页码为 {total_pages}")

    start_index = (page - 1) * page_size
    page_items = all_items[start_index : start_index + page_size]
    return page_items, total, total_pages, start_index


def delete_local_storage_item(
    target_type: str,
    target_id: int | None,
    kind: str,
    index: int,
) -> str:
    if index < 1:
        raise ValueError("序号必须大于等于 1")

    target_key = build_target_key(target_type, target_id)
    paths = _ensure_target_paths(target_key)

    if kind == "avatar":
        all_items = _list_uploaded_images(target_key)
        if index > len(all_items):
            raise ValueError(f"头像序号超出范围，当前最大序号为 {len(all_items)}")

        removed_value = all_items[index - 1]
        removed_name = Path(removed_value).name
        remaining_list = [
            item
            for item in _read_lines(paths["uploaded_avatar_list_file"])
            if item != removed_value and Path(item).exists()
        ]
        _rewrite_lines(paths["uploaded_avatar_list_file"], remaining_list)

        removed_path = Path(removed_value)
        if removed_path.exists():
            try:
                removed_path.unlink()
            except OSError as exception:
                raise ValueError(f"删除头像文件失败: {exception}") from exception

        _prune_selection_history(target_key, "avatar", removed_value)
        return removed_name

    if kind == "name":
        all_items = _list_uploaded_names(target_key)
        if index > len(all_items):
            raise ValueError(f"名称序号超出范围，当前最大序号为 {len(all_items)}")

        removed_value = all_items[index - 1]
        remaining_list = [item for item in all_items if item != removed_value]
        _rewrite_lines(paths["uploaded_names_file"], remaining_list)
        _prune_selection_history(target_key, "name", removed_value)
        return removed_value

    raise ValueError("不支持的存储列表类型")


async def _prepare_remote_single_image(source: str, target_key: str) -> str:
    paths = _ensure_target_paths(target_key)
    suffix = _path_suffix_from_url(source) or ".jpg"
    destination = paths["remote_avatar_dir"] / _safe_filename_from_url(source, suffix)
    content = await _download_bytes(source)
    _validate_image_content(content)
    await _write_bytes(destination, content)
    return _normalize_path_value(destination)


async def _load_avatar_manifest(source: str) -> list[str]:
    entries = _validate_manifest_lines(
        await _read_text_source_lines(source),
        kind="头像",
    )
    if not entries:
        raise ValueError("头像清单为空")

    invalid_entries = [entry for entry in entries if not _is_image_reference(entry)]
    if invalid_entries:
        raise ValueError("头像清单中存在非图片资源条目")

    return _deduplicate_preserving_order(entries)


async def _load_name_manifest(source: str) -> list[str]:
    entries = _validate_manifest_lines(
        await _read_text_source_lines(source),
        kind="名称",
    )
    if not entries:
        raise ValueError("名称清单为空")

    return _deduplicate_preserving_order(
        [_validate_name_value(entry) for entry in entries]
    )


async def _materialize_avatar_candidate(target_key: str, candidate: str) -> str:
    if _is_remote_url(candidate):
        return await _prepare_remote_single_image(candidate, target_key)

    return await _validate_local_image_file(Path(candidate))


def _select_candidate(
    target_key: str,
    kind: str,
    candidates: list[str],
    scheduled: bool,
) -> str:
    unique_candidates = _deduplicate_preserving_order(candidates)
    if not unique_candidates:
        raise ValueError("资源池为空")

    if not scheduled:
        return random.choice(unique_candidates)

    state = _load_selection_state()
    target_state = state.setdefault(
        target_key,
        {"avatar_history": [], "name_history": []},
    )
    history_key = f"{kind}_history"
    history = [
        item
        for item in target_state.get(history_key, [])
        if item in unique_candidates
    ]
    recent_length = max(len(unique_candidates) - 1, 0)
    recent_history = history[-recent_length:] if recent_length else []
    available_candidates = [
        candidate for candidate in unique_candidates if candidate not in recent_history
    ]
    if not available_candidates:
        available_candidates = unique_candidates

    selected = random.choice(available_candidates)
    if recent_length:
        target_state[history_key] = (recent_history + [selected])[-recent_length:]
    else:
        target_state[history_key] = []
    _save_selection_state()
    return selected


async def resolve_avatar_resource(
    source: str | None,
    target_type: str,
    target_id: int | None,
    scheduled: bool,
) -> str | None:
    target_key = build_target_key(target_type, target_id)

    if source is None:
        uploaded_images = _list_uploaded_images(target_key)
        if not uploaded_images:
            return None
        selected = _select_candidate(target_key, "avatar", uploaded_images, scheduled)
        return await _materialize_avatar_candidate(target_key, selected)

    if _looks_like_directory_source(source):
        source_candidates = _list_local_image_files(Path(source))

        all_candidates = source_candidates + _list_uploaded_images(target_key)
        if not all_candidates:
            return None
        selected = _select_candidate(target_key, "avatar", all_candidates, scheduled)
        return await _materialize_avatar_candidate(target_key, selected)

    if _looks_like_txt_source(source):
        source_type = await classify_source_token(source)
        if source_type != "avatar_manifest":
            raise ValueError("提供的 txt 清单不是头像图片清单")

        source_candidates = await _load_avatar_manifest(source)

        all_candidates = source_candidates + _list_uploaded_images(target_key)
        if not all_candidates:
            return None
        selected = _select_candidate(target_key, "avatar", all_candidates, scheduled)
        return await _materialize_avatar_candidate(target_key, selected)

    if _looks_like_image_file_source(source):
        return await _materialize_avatar_candidate(target_key, source)

    raise ValueError("头像资源必须是图片文件、本地目录或图片清单 txt")


async def resolve_name_resource(
    source: str | None,
    target_type: str,
    target_id: int | None,
    scheduled: bool,
) -> str | None:
    target_key = build_target_key(target_type, target_id)

    if source is None:
        uploaded_names = _list_uploaded_names(target_key)
        if not uploaded_names:
            return None
        return _select_candidate(target_key, "name", uploaded_names, scheduled)

    if _looks_like_txt_source(source):
        source_type = await classify_source_token(source)
        if source_type != "name_manifest":
            raise ValueError("提供的 txt 清单不是名称清单")

        source_names = await _load_name_manifest(source)

        all_candidates = source_names + _list_uploaded_names(target_key)
        if not all_candidates:
            return None
        return _select_candidate(target_key, "name", all_candidates, scheduled)

    return _validate_name_value(source)
