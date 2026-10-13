# DogBuddyCn — file manager routes (L1 Interface)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""Explorer-style file management API — thin shells over L2 FileService."""
from __future__ import annotations

import base64

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel


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

    @r.get("/files/image", dependencies=auth_deps)
    def image(path: str, workspace: str | None = None) -> dict:
        try:
            data = _fs(workspace).read(path, workspace)
        except (PermissionError, FileNotFoundError) as e:
            raise HTTPException(status_code=403, detail=str(e)) from e
        return {"image_base64": data.get("image_base64", "")}

    @r.post("/files/upload", dependencies=auth_deps)
    async def upload(file: UploadFile = File(...), path: str = Form("")) -> dict:
        data = await file.read()
        rel = f"{path.rstrip('/')}/{file.filename}" if path else file.filename
        try:
            size = svc.files.save_upload(rel, data)
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
