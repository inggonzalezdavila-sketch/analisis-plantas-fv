"""Galería persistente: Apps Script/Drive en Render y SQLite en uso local."""
from __future__ import annotations

import base64
import os
import re
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path

import google_sheets

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "galeria.sqlite3"
ALLOWED_CATEGORIES = {"Residencial", "Comercial", "Industrial", "Institucional", "Otro"}
MAX_IMAGE_BYTES = 2_000_000
MAX_THUMBNAIL_BYTES = 32_000


def _remote() -> bool:
    return os.environ.get("QUOTE_STORAGE_BACKEND", "").strip().lower() == "apps_script" or bool(os.environ.get("PORT")) or google_sheets.configured()


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("""CREATE TABLE IF NOT EXISTS gallery_items (
        id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL,
        location TEXT NOT NULL, category TEXT NOT NULL, published INTEGER NOT NULL,
        is_cover INTEGER NOT NULL, sort_order INTEGER NOT NULL, created_at TEXT NOT NULL,
        image BLOB NOT NULL, thumbnail BLOB NOT NULL
    )""")
    db.commit()
    return db


@contextmanager
def _session():
    db = _connect()
    try:
        with db:
            yield db
    finally:
        db.close()


def _decode_jpeg(value: object, label: str, limit: int, max_dimension: int) -> bytes:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9+/]+={0,2}", value):
        raise ValueError(f"La imagen {label} no tiene un formato válido.")
    try:
        data = base64.b64decode(value, validate=True)
    except (ValueError, base64.binascii.Error):
        raise ValueError(f"La imagen {label} no se pudo leer.") from None
    if not 4 < len(data) <= limit or not data.startswith(b"\xff\xd8") or not data.endswith(b"\xff\xd9"):
        raise ValueError(f"La imagen {label} debe ser JPEG y no superar {limit // 1_000_000 or 1} MB.")

    # Lee las dimensiones del encabezado JPEG sin depender de bibliotecas del sistema.
    position = 2
    width = height = 0
    frame_markers = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
    while position + 4 < len(data):
        if data[position] != 0xFF:
            position += 1
            continue
        while position < len(data) and data[position] == 0xFF:
            position += 1
        if position >= len(data):
            break
        marker = data[position]
        position += 1
        if marker in {0xD8, 0xD9} or 0xD0 <= marker <= 0xD7:
            continue
        if position + 2 > len(data):
            break
        segment_size = int.from_bytes(data[position:position + 2], "big")
        if segment_size < 2 or position + segment_size > len(data):
            break
        if marker in frame_markers and segment_size >= 7:
            height = int.from_bytes(data[position + 3:position + 5], "big")
            width = int.from_bytes(data[position + 5:position + 7], "big")
            break
        position += segment_size
    if not width or not height or max(width, height) > max_dimension or width * height > max_dimension * max_dimension:
        raise ValueError(f"La imagen {label} no tiene dimensiones válidas u optimizadas.")
    return data


def normalize_item(payload: object, *, new: bool) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("Los datos de la fotografía no son válidos.")
    title = str(payload.get("title", "")).strip()
    description = str(payload.get("description", "")).strip()
    location = str(payload.get("location", "")).strip()
    category = str(payload.get("category", "Otro")).strip()
    if not title or len(title) > 160:
        raise ValueError("Escribe un título de máximo 160 caracteres.")
    if len(description) > 1000 or len(location) > 180 or category not in ALLOWED_CATEGORIES:
        raise ValueError("Revisa la descripción, ubicación o categoría de la fotografía.")
    published = payload.get("published", True)
    is_cover = payload.get("isCover", False)
    if not isinstance(published, bool) or not isinstance(is_cover, bool):
        raise ValueError("El estado de publicación o portada no es válido.")
    if is_cover and not published:
        raise ValueError("Publica la fotografía antes de elegirla como portada.")

    item = {
        "id": uuid.uuid4().hex if new else "",
        "title": title, "description": description, "location": location,
        "category": category, "published": published, "isCover": is_cover,
    }
    if new or payload.get("imageData"):
        item["imageData"] = base64.b64encode(_decode_jpeg(payload.get("imageData"), "principal", MAX_IMAGE_BYTES, 2000)).decode("ascii")
        item["thumbnailData"] = base64.b64encode(_decode_jpeg(payload.get("thumbnailData"), "miniatura", MAX_THUMBNAIL_BYTES, 600)).decode("ascii")
    return item


def list_items(include_hidden: bool = False) -> list[dict]:
    if _remote():
        items = google_sheets.gallery_list(include_hidden)
        return sorted(items, key=lambda item: int(item.get("sortOrder", 0)))
    with _session() as db:
        rows = db.execute(
            "SELECT id,title,description,location,category,published,is_cover,sort_order,created_at,thumbnail FROM gallery_items "
            + ("" if include_hidden else "WHERE published=1 ")
            + "ORDER BY sort_order ASC, created_at DESC"
        ).fetchall()
    return [{"id": row["id"], "title": row["title"], "description": row["description"],
             "location": row["location"], "category": row["category"], "published": bool(row["published"]),
             "isCover": bool(row["is_cover"]), "sortOrder": row["sort_order"], "createdAt": row["created_at"],
             "thumbnailData": base64.b64encode(row["thumbnail"]).decode("ascii")} for row in rows]


def create_item(item: dict) -> dict:
    if _remote():
        saved = google_sheets.gallery_create(item)
        if not saved:
            raise google_sheets.GoogleSheetsError("Apps Script no confirmó el guardado de la fotografía.")
        return saved
    from datetime import datetime, timezone
    with _session() as db:
        if item["isCover"]:
            db.execute("UPDATE gallery_items SET is_cover=0")
        sort_order = int(db.execute("SELECT COALESCE(MAX(sort_order),-1)+1 FROM gallery_items").fetchone()[0])
        image = base64.b64decode(item["imageData"])
        thumbnail = base64.b64decode(item["thumbnailData"])
        created_at = datetime.now(timezone.utc).isoformat()
        db.execute("INSERT INTO gallery_items VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
            item["id"], item["title"], item["description"], item["location"], item["category"],
            int(item["published"]), int(item["isCover"]), sort_order, created_at, image, thumbnail,
        ))
        item.update(sortOrder=sort_order, createdAt=created_at)
    return item


def update_item(item_id: str, item: dict) -> dict:
    if _remote():
        saved = google_sheets.gallery_update(item_id, item)
        if not saved:
            raise google_sheets.GoogleSheetsError("Apps Script no confirmó la actualización de la fotografía.")
        return saved
    with _session() as db:
        row = db.execute("SELECT image,thumbnail FROM gallery_items WHERE id=?", (item_id,)).fetchone()
        if not row:
            raise ValueError("La fotografía ya no está en la galería.")
        image = base64.b64decode(item["imageData"]) if item.get("imageData") else row["image"]
        thumbnail = base64.b64decode(item["thumbnailData"]) if item.get("thumbnailData") else row["thumbnail"]
        if item["isCover"]:
            db.execute("UPDATE gallery_items SET is_cover=0")
        db.execute("""UPDATE gallery_items SET title=?,description=?,location=?,category=?,published=?,is_cover=?,image=?,thumbnail=? WHERE id=?""",
                   (item["title"], item["description"], item["location"], item["category"], int(item["published"]),
                    int(item["isCover"]), image, thumbnail, item_id))
    return {**item, "id": item_id}


def delete_item(item_id: str) -> None:
    if _remote():
        google_sheets.gallery_delete(item_id)
        return
    with _session() as db:
        db.execute("DELETE FROM gallery_items WHERE id=?", (item_id,))


def reorder_items(item_ids: list[str]) -> None:
    if _remote():
        google_sheets.gallery_reorder(item_ids)
        return
    with _session() as db:
        current = {row[0] for row in db.execute("SELECT id FROM gallery_items")}
        if len(item_ids) != len(current) or set(item_ids) != current:
            raise ValueError("La lista cambió. Recarga la galería y vuelve a ordenar.")
        db.executemany("UPDATE gallery_items SET sort_order=? WHERE id=?", [(index, item_id) for index, item_id in enumerate(item_ids)])


def get_image(item_id: str, variant: str = "original", include_hidden: bool = False) -> tuple[bytes, str]:
    if variant not in {"original", "thumbnail"}:
        raise ValueError("La variante de imagen no es válida.")
    if _remote():
        result = google_sheets.gallery_image(item_id, variant, include_hidden)
        try:
            return base64.b64decode(result.get("data", ""), validate=True), str(result.get("mimeType") or "image/jpeg")
        except (ValueError, base64.binascii.Error):
            raise google_sheets.GoogleSheetsError("Drive devolvió una imagen con formato inválido.") from None
    with _session() as db:
        row = db.execute("SELECT image,thumbnail,published FROM gallery_items WHERE id=?", (item_id,)).fetchone()
    if not row or (not include_hidden and not row["published"]):
        raise ValueError("La fotografía no existe.")
    return bytes(row["thumbnail"] if variant == "thumbnail" else row["image"]), "image/jpeg"
