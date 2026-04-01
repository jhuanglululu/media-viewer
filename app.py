from pathlib import Path
import os, sys, gzip, secrets, io, zipfile
from fastapi import Depends, FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from PIL import Image

from db import *
from templates import TEMPLATES
from uploads import UPLOAD_DIR

app = FastAPI()

ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "NONE")
if ADMIN_TOKEN == "NONE":
    print("ADMIN_TOKEN environment variable not set")
    sys.exit(1)


async def require_admin(request: Request):
    if request.cookies.get("token") != ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="Forbidden")


def collect_files(folder_name: str, prefix: str = "") -> list[tuple[str, dict]]:
    """Recursively collect all files from a folder, returning (zip_path, file_row) pairs."""
    result = []
    items = fetch_folder(folder_name)
    if items is None:
        return result
    for item in items:
        if item["item_type"] == "file":
            row = fetch_file(item["item_name"])
            if row:
                result.append((prefix + item["item_name"], row))
        elif item["item_type"] == "folder":
            result.extend(
                collect_files(item["item_name"], prefix + item["item_name"] + "/")
            )
    return result


# login


@app.get("/login")
async def login_get(request: Request):
    return TEMPLATES.TemplateResponse(request, "login.html")


@app.post("/login")
async def login_post(request: Request):
    form = await request.form()
    token = form.get("token")
    if not isinstance(token, str):
        raise HTTPException(422, "token must be a string")

    if not secrets.compare_digest(token, ADMIN_TOKEN):
        raise HTTPException(403)

    response = RedirectResponse("/admin", status_code=303)
    response.set_cookie(
        key="token",
        value=token,
        httponly=True,
        secure=True,
        samesite="strict",
        max_age=60 * 60 * 24 * 365,
    )
    return response


@app.get("/admin", response_class=HTMLResponse)
async def admin_get(request: Request, _=Depends(require_admin)):
    files, folders = fetch_all()
    return TEMPLATES.TemplateResponse(
        request, "admin.html", {"files": files, "folders": folders}
    )


# files


@app.post("/upload/{name}")
async def upload_post(
    file: UploadFile, name: str, request: Request, _=Depends(require_admin)
):
    form = await request.form()
    caption = form.get("caption", "")
    if not isinstance(caption, str):
        caption = ""
    await save_file(file, name, caption)


@app.post("/delete/{name}")
async def delete_post(name: str, _=Depends(require_admin)):
    delete_file(name)


# folders


@app.post("/create-folder/{name}")
async def create_folder_post(name: str, _=Depends(require_admin)):
    create_folder(name)


@app.post("/folder/{name}/add")
async def add_to_folder_post(name: str, request: Request, _=Depends(require_admin)):
    form = await request.form()
    item_name = form.get("item_name")
    item_type = form.get("item_type")
    if not isinstance(item_name, str) or not isinstance(item_type, str):
        raise HTTPException(422)
    if not add_to_folder(name, item_name, item_type):
        raise HTTPException(400, "Item not found or would create a cycle")


@app.post("/folder/{name}/remove/{item}")
async def remove_from_folder_post(name: str, item: str, _=Depends(require_admin)):
    remove_from_folder(name, item)


@app.post("/delete-folder/{name}")
async def delete_folder_post(name: str, _=Depends(require_admin)):
    delete_folder(name)


# user


@app.get("/folder/{name}/zip")
async def download_zip(name: str):
    items = fetch_folder(name)
    if items is None:
        raise HTTPException(404)

    files = collect_files(name)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for zip_path, row in files:
            path = UPLOAD_DIR / row["filename"]
            data = path.read_bytes()
            if row["filename"].endswith(".gz"):
                data = gzip.decompress(data)
            ext = (
                "." + row["extension"]
                if not zip_path.endswith(row["extension"])
                else ""
            )
            zf.writestr(zip_path + ext, data)
    buf.seek(0)
    return Response(
        content=buf.read(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{name}.zip"'},
    )


@app.get("/image/{name}")
async def image_get(name: str):
    row = fetch_file(name)
    if not row or not row["media_type"].startswith("image/"):
        raise HTTPException(404)
    return FileResponse(
        UPLOAD_DIR / row["filename"],
        media_type=row["media_type"],
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.get("/{name}/download")
async def download_get(name: str):
    row = fetch_file(name)
    if not row:
        raise HTTPException(404)

    if row["media_type"] == "image/webp":
        img = Image.open(UPLOAD_DIR / row["filename"])
        buf = io.BytesIO()
        img.save(buf, "PNG")
        buf.seek(0)
        return Response(
            content=buf.read(),
            media_type="image/png",
            headers={"Content-Disposition": f'attachment; filename="{name}.png"'},
        )
    elif row["media_type"] == "image/gif":
        return FileResponse(
            UPLOAD_DIR / row["filename"],
            media_type="image/gif",
            headers={"Content-Disposition": f'attachment; filename="{name}.gif"'},
        )
    else:
        ext = row["extension"]
        return Response(
            content=(UPLOAD_DIR / row["filename"]).read_bytes(),
            media_type=row["media_type"],
            headers={
                "Content-Disposition": f'attachment; filename="{name}.{ext}"',
                "Content-Encoding": "gzip",
            },
        )


@app.get("/{name}")
async def view_get(name: str, request: Request):
    row = fetch_file(name)
    if row:
        if row["media_type"].startswith("image/"):
            return TEMPLATES.TemplateResponse(
                request,
                "image.html",
                {
                    "name": name,
                    "caption": row["caption"],
                },
                headers={"Cache-Control": "public, max-age=86400"},
            )
        path = UPLOAD_DIR / row["filename"]
        return Response(
            content=path.read_bytes(),
            media_type=row["media_type"],
            headers={
                "Content-Encoding": "gzip",
                "Cache-Control": "public, max-age=86400",
            },
        )

    folder = fetch_folder(name)
    if folder is not None:
        return TEMPLATES.TemplateResponse(
            request,
            "folder.html",
            {"name": name, "items": folder},
            headers={"Cache-Control": "public, max-age=86400"},
        )

    raise HTTPException(404)
