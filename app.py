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

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for item in items:
            if item["item_type"] == "file":
                row = fetch_file(item["item_name"])
                if row:
                    path = UPLOAD_DIR / row["filename"]
                    data = path.read_bytes()
                    # decompress gzipped files for the zip
                    if row["filename"].endswith(".gz"):
                        data = gzip.decompress(data)
                    ext = Path(row["filename"]).suffix.replace(".gz", "")
                    zf.writestr(item["item_name"] + ext, data)
    buf.seek(0)
    return Response(
        content=buf.read(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{name}.zip"'},
    )


@app.get("/image/{name}")
async def image_get(name: str):
    row = fetch_file(name)
    if not row or row["media_type"] != "image/webp":
        raise HTTPException(404)
    return FileResponse(UPLOAD_DIR / row["filename"], media_type="image/webp")


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
        if row["media_type"] == "image/webp":
            return TEMPLATES.TemplateResponse(
                request, "image.html", {"name": name, "caption": row["caption"]}
            )
        path = UPLOAD_DIR / row["filename"]
        return Response(
            content=path.read_bytes(),
            media_type=row["media_type"],
            headers={"Content-Encoding": "gzip"},
        )

    folder = fetch_folder(name)
    if folder is not None:
        return TEMPLATES.TemplateResponse(
            request, "folder.html", {"name": name, "items": folder}
        )

    raise HTTPException(404)
