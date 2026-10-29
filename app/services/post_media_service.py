"""Post-owned media validation, isolated storage, and authorization helpers."""
from __future__ import annotations

import io
import zipfile
from pathlib import Path, PurePath
from uuid import UUID, uuid4

from PIL import Image, UnidentifiedImageError
from sqlalchemy.orm import Session

from app.database.models.social import PostMedia
from app.database.types import utc_now
from app.services.post_service import PostError, get_owned_post

IMAGE_MIMES = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
DOCUMENT_MIMES = {"application/pdf": "pdf", "text/plain": "txt", "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx"}
VIDEO_MIMES = {"video/mp4": "mp4"}


def _safe_filename(filename: str) -> str:
    name = PurePath(filename or "").name
    if not name or name != filename or any(part in filename for part in ("..", "/", "\\")):
        raise PostError("INVALID_POST_MEDIA", "Invalid attachment filename.")
    return name[:255]


def _classify(data: bytes, mime: str, settings):
    if mime in IMAGE_MIMES:
        if len(data) > settings.post_image_max_bytes:
            raise PostError("INVALID_POST_MEDIA", "Image exceeds the size limit.")
        try:
            with Image.open(io.BytesIO(data)) as image:
                image.verify()
            with Image.open(io.BytesIO(data)) as image:
                width, height = image.size
                detected = (image.format or "").lower()
        except (UnidentifiedImageError, OSError) as exc:
            raise PostError("INVALID_POST_MEDIA", "Invalid image content.") from exc
        expected = {"image/jpeg": "jpeg", "image/png": "png", "image/webp": "webp"}[mime]
        if detected != expected or width * height > settings.post_image_max_pixels:
            raise PostError("INVALID_POST_MEDIA", "Image signature or dimensions are invalid.")
        return "IMAGE", IMAGE_MIMES[mime], width, height, None
    if mime in DOCUMENT_MIMES:
        if len(data) > settings.post_document_max_bytes:
            raise PostError("INVALID_POST_MEDIA", "Document exceeds the size limit.")
        ext = DOCUMENT_MIMES[mime]
        valid = data.startswith(b"%PDF-") if ext == "pdf" else True
        if ext == "docx":
            try:
                with zipfile.ZipFile(io.BytesIO(data)) as archive:
                    valid = "[Content_Types].xml" in archive.namelist() and any(n.startswith("word/") for n in archive.namelist())
            except zipfile.BadZipFile:
                valid = False
        if ext == "txt":
            try: data.decode("utf-8")
            except UnicodeDecodeError: valid = False
        if not valid:
            raise PostError("INVALID_POST_MEDIA", "Document signature does not match its MIME type.")
        return "DOCUMENT", ext, None, None, None
    if mime in VIDEO_MIMES:
        if len(data) > settings.post_video_max_bytes or len(data) < 12 or data[4:8] != b"ftyp":
            raise PostError("INVALID_POST_MEDIA", "Invalid or oversized MP4 video.")
        return "VIDEO", "mp4", None, None, None
    raise PostError("INVALID_POST_MEDIA", "Unsupported attachment MIME type.")


def _storage_path(root: str, user_id: str, post_id: str, media_id: str, ext: str) -> Path:
    for value in (user_id, post_id, media_id): UUID(value)
    return Path(root) / "users" / user_id / "posts" / post_id / media_id / f"original.{ext}"


def add_media(db: Session, post_id: str, user_id: str, filename: str, mime: str, data: bytes, settings) -> PostMedia:
    from app.services.user_restriction_service import assert_can_upload_media
    post = get_owned_post(db, post_id, user_id, lock=True)
    assert_can_upload_media(db, user_id)
    if post.status != "DRAFT":
        raise PostError("POST_NOT_EDITABLE", "Attachments can only be added to a draft post.")
    active = [item for item in post.media if item.deleted_at is None]
    if len(active) >= settings.post_max_attachments:
        raise PostError("POST_MEDIA_LIMIT_EXCEEDED", "Attachment limit exceeded.")
    media_type, ext, width, height, duration = _classify(data, mime, settings)
    counts = {kind: sum(item.media_type == kind for item in active) for kind in ("IMAGE", "DOCUMENT", "VIDEO")}
    limits = {"IMAGE": settings.post_max_images, "DOCUMENT": settings.post_max_documents, "VIDEO": settings.post_max_videos}
    if counts[media_type] >= limits[media_type]:
        raise PostError("POST_MEDIA_LIMIT_EXCEEDED", f"{media_type.title()} attachment limit exceeded.")
    media_id = str(uuid4()); path = _storage_path(settings.document_storage_root, user_id, post.id, media_id, ext)
    path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
    key = str(path.relative_to(Path(settings.document_storage_root)))
    now = utc_now()
    media = PostMedia(id=media_id, post_id=post.id, media_type=media_type, storage_key=key,
                      display_filename=_safe_filename(filename), mime_type=mime, file_size=len(data),
                      position=len(active), processing_status="READY", width=width, height=height,
                      duration_seconds=duration, created_at=now, updated_at=now)
    db.add(media); post.updated_at = now; db.commit(); db.refresh(media)
    return media


def resolve_media_path(media: PostMedia, settings) -> Path | None:
    root = Path(settings.document_storage_root).resolve()
    candidate = (root / media.storage_key).resolve()
    try: candidate.relative_to(root)
    except ValueError: return None
    return candidate if candidate.is_file() else None


def delete_draft_media(db: Session, post_id: str, media_id: str, user_id: str) -> None:
    post = get_owned_post(db, post_id, user_id)
    if post.status != "DRAFT": raise PostError("POST_NOT_EDITABLE", "Published media cannot be changed.")
    media = next((m for m in post.media if m.id == media_id and m.deleted_at is None), None)
    if not media: raise PostError("POST_NOT_FOUND", "Media not found.")
    media.deleted_at = media.updated_at = utc_now(); db.commit()
