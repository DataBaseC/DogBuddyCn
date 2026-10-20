# DogBuddyCn — file manager routes (L1 Interface)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""Explorer-style file management API — thin shells over L2 FileService."""
from __future__ import annotations

import base64
import logging

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

logger = logging.getLogger("dogbuddy.web.files")


class MkdirIn(BaseModel):
    path: str
    name: str
    workspace: str | None = None


class RenameIn(BaseModel):
    path: str
    new_name: str
    workspace: str | None = None


class DeleteIn(BaseModel):
    path: str
    workspace: str | None = None


class WriteIn(BaseModel):
    path: str
    content: str
    workspace: str | None = None


def router(svc, auth_deps) -> APIRouter:
    r = APIRouter(tags=["files"])

    def _fs(workspace: str | None):
        if workspace:
            svc.files._root_for(workspace)  # validates
        return svc.files

    @r.get("/files/tree", dependencies=auth_deps)
    def tree(path: str = "", workspace: str | None = None) -> list[dict]:
        try:
            return _fs(workspace).tree(path, workspace)
        except (PermissionError, FileNotFoundError) as e:
            raise HTTPException(status_code=403, detail=str(e)) from e

    @r.get("/files/list", dependencies=auth_deps)
    def list_dir(path: str = "", workspace: str | None = None) -> dict:
        try:
            return _fs(workspace).list(path, workspace)
        except (PermissionError, FileNotFoundError) as e:
            raise HTTPException(status_code=403, detail=str(e)) from e

    @r.get("/files/read", dependencies=auth_deps)
    def read(path: str, workspace: str | None = None) -> dict:
        try:
            return _fs(workspace).read(path, workspace)
        except PermissionError as e:
            raise HTTPException(status_code=403, detail=str(e)) from e
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail="file not found") from e
        except OverflowError as e:
            raise HTTPException(status_code=413, detail=str(e)) from e
        except ValueError as e:
            raise HTTPException(status_code=415, detail=str(e)) from e

    @r.get("/files/download", dependencies=auth_deps)
    def download(path: str, workspace: str | None = None):
        try:
            target, name = _fs(workspace).read_bytes(path, workspace)
        except (PermissionError, FileNotFoundError) as e:
            raise HTTPException(status_code=403, detail=str(e)) from e
        return FileResponse(str(target), filename=name)

    @r.get("/files/zip", dependencies=auth_deps)
    def zip_dir(path: str = "", workspace: str | None = None,
                background_tasks: BackgroundTasks = None):
        """目录打包下载（v0.10.39）：tar.gz 流 + 发完自动删临时文件。

        owner："项目文件夹目前不能打包一起下载"。空 path = 整个工作区根。
        排除 .git/.venv/__pycache__/node_modules（与 list/tree 口径一致）。
        """
        tmp = None
        try:
            tmp, name = _fs(workspace).zip_dir(path, workspace)
        except PermissionError as e:
            raise HTTPException(status_code=403, detail=str(e)) from e
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except ValueError as e:  # 体积超限等业务拒绝 → 400（不是服务器错误）
            raise HTTPException(status_code=400, detail=str(e)) from e
        except Exception as e:  # noqa: BLE001 — 打包失败给 500，别把临时文件留在盘上
            if tmp is not None:
                try:
                    tmp.unlink()
                except OSError:
                    pass
            raise HTTPException(status_code=500, detail=f"zip failed: {e}") from e

        def _cleanup() -> None:
            try:
                tmp.unlink()
            except OSError:
                logger.debug("zip temp cleanup failed", exc_info=True)

        if background_tasks is not None:
            background_tasks.add_task(_cleanup)
        return FileResponse(str(tmp), filename=name, media_type="application/gzip")

    @r.get("/files/image", dependencies=auth_deps)
    def image(path: str, workspace: str | None = None) -> dict:
        try:
            data = _fs(workspace).read(path, workspace)
        except (PermissionError, FileNotFoundError) as e:
            raise HTTPException(status_code=403, detail=str(e)) from e
        return {"image_base64": data.get("image_base64", "")}

    @r.post("/files/upload", dependencies=auth_deps)
    async def upload(file: UploadFile = File(...), path: str = Form(""),
                     workspace: str = Form("")) -> dict:
        data = await file.read()
        rel = f"{path.rstrip('/')}/{file.filename}" if path else file.filename
        try:
            # v0.10.41：上传也要认 workspace——否则跳转到项目/工位工作区后，
            # 拖拽上传会静默落到默认根目录（与读路径不一致）
            size = svc.files.save_upload(rel, data, workspace or None)
        except PermissionError as e:
            raise HTTPException(status_code=403, detail=str(e)) from e
        return {"uploaded": rel, "size": size}

    @r.post("/files/mkdir", dependencies=auth_deps)
    def mkdir(body: MkdirIn) -> dict:
        try:
            return {"created": svc.files.mkdir(body.path, body.name, body.workspace)}
        except PermissionError as e:
            raise HTTPException(status_code=403, detail=str(e)) from e

    @r.post("/files/rename", dependencies=auth_deps)
    def rename(body: RenameIn) -> dict:
        try:
            return {"renamed": svc.files.rename(body.path, body.new_name, body.workspace)}
        except (PermissionError, ValueError) as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @r.post("/files/delete", dependencies=auth_deps)
    def delete(body: DeleteIn) -> dict:
        try:
            svc.files.delete(body.path, body.workspace)
        except PermissionError as e:
            raise HTTPException(status_code=403, detail=str(e)) from e
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail="not found") from e
        return {"deleted": True}

    @r.post("/files/write", dependencies=auth_deps)
    def write(body: WriteIn) -> dict:
        try:
            size = svc.files.write(body.path, body.content, body.workspace)
        except PermissionError as e:
            raise HTTPException(status_code=403, detail=str(e)) from e
        return {"saved": True, "size": size}

    return r
