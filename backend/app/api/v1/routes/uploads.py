from fastapi import UploadFile

from app.core.errors import AppError
from app.services.job_service import UploadPayload


async def read_upload(upload: UploadFile, max_bytes: int) -> UploadPayload:
    try:
        content = await upload.read(max_bytes + 1)
    finally:
        await upload.close()
    if len(content) > max_bytes:
        raise AppError(
            status=422,
            code="IMAGE_TOO_LARGE",
            title="Image validation failed",
            detail="画像ファイルサイズが上限を超えています。",
        )
    return UploadPayload(content=content, declared_media_type=upload.content_type)
