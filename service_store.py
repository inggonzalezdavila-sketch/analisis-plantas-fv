"""Persistencia del portafolio de servicios en Google Sheets/Drive o SQLite local."""
from __future__ import annotations

import base64
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import google_sheets
import gallery_store

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "servicios.sqlite3"
MAX_IMAGE_BYTES = 2_000_000
MAX_THUMBNAIL_BYTES = 32_000

DEFAULT_SERVICES = [
    {"name": "Diseño e instalación de sistemas solares", "description": "Diseñamos e implementamos soluciones fotovoltaicas adaptadas a las necesidades energéticas de hogares, comercios, industrias y comunidades.", "services": ["Sistemas solares On-Grid.", "Sistemas solares Off-Grid.", "Sistemas híbridos con almacenamiento.", "Dimensionamiento y estudios de viabilidad.", "Ampliación y modernización de instalaciones."]},
    {"name": "Operación y mantenimiento (O&M)", "description": "Mantenemos los sistemas fotovoltaicos en condiciones óptimas de funcionamiento, confiabilidad y desempeño energético.", "services": ["Mantenimiento preventivo y correctivo.", "Diagnóstico de inversores y cadenas fotovoltaicas.", "Inspecciones eléctricas y termográficas.", "Pruebas y puesta en marcha.", "Optimización de rendimiento."]},
    {"name": "Ingeniería y servicios eléctricos", "description": "Desarrollamos soluciones de ingeniería para instalaciones eléctricas seguras, eficientes y confiables.", "services": ["Diseño de instalaciones eléctricas.", "Montaje y adecuación de tableros.", "Sistemas de protección y puesta a tierra.", "Diagnóstico de calidad de energía.", "Mantenimiento de instalaciones eléctricas."]},
    {"name": "Consultoría y legalización de proyectos", "description": "Acompañamos los procesos técnicos y documentales necesarios para la conexión y legalización de proyectos de generación solar.", "services": ["Trámites AGPE y GD.", "Gestión ante operadores de red.", "Memorias de cálculo y planos.", "Asesoría técnica para cumplimiento RETIE.", "Consultoría energética y regulatoria."]},
    {"name": "Monitoreo y análisis energético", "description": "Utilizamos herramientas digitales y análisis de datos para supervisar el desempeño de los sistemas fotovoltaicos y detectar oportunidades de mejora.", "services": ["Monitoreo de plantas solares.", "Análisis de generación y consumo.", "Evaluación de autoconsumo y excedentes.", "Diagnóstico de pérdidas de rendimiento.", "Elaboración de informes técnicos."]},
    {"name": "Automatización y comunicaciones", "description": "Integramos equipos, sistemas de medición y plataformas digitales para mejorar el control y supervisión de instalaciones energéticas.", "services": ["Configuración de inversores.", "Integración de SmartLogger y medidores.", "Comunicación RS485, Modbus y Ethernet.", "Configuración de cero exportación.", "Integración de sistemas de supervisión y control."]},
]


def _remote() -> bool:
    return os.environ.get("QUOTE_STORAGE_BACKEND", "").strip().lower() == "apps_script" or bool(os.environ.get("PORT")) or google_sheets.configured()


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("""CREATE TABLE IF NOT EXISTS service_items (
        id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL,
        services TEXT NOT NULL, published INTEGER NOT NULL, sort_order INTEGER NOT NULL,
        created_at TEXT NOT NULL, image BLOB, thumbnail BLOB
    )""")
    db.execute("CREATE TABLE IF NOT EXISTS service_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
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


def _public_item(row: sqlite3.Row) -> dict:
    return {"id": row["id"], "name": row["name"], "description": row["description"],
            "services": json.loads(row["services"]), "published": bool(row["published"]),
            "sortOrder": row["sort_order"], "createdAt": row["created_at"], "imageAvailable": row["image"] is not None,
            "thumbnailData": base64.b64encode(row["thumbnail"]).decode("ascii") if row["thumbnail"] else ""}


def normalize_service(payload: object, *, new: bool) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("Los datos del servicio no son válidos.")
    name = str(payload.get("name", "")).strip()
    description = str(payload.get("description", "")).strip()
    raw_services = payload.get("services", [])
    if isinstance(raw_services, str):
        raw_services = raw_services.splitlines()
    if not isinstance(raw_services, list):
        raise ValueError("Escribe los servicios como una lista, uno por línea.")
    services = [str(value).strip() for value in raw_services if str(value).strip()]
    published = payload.get("published", True)
    if not name or len(name) > 140 or not description or len(description) > 1200 or not 1 <= len(services) <= 20 or any(len(value) > 220 for value in services):
        raise ValueError("Revisa el nombre, descripción y lista de servicios (máximo 20 elementos).")
    if not isinstance(published, bool):
        raise ValueError("El estado de publicación no es válido.")
    item = {"id": uuid.uuid4().hex if new else "", "name": name, "description": description, "services": services, "published": published}
    image_data = payload.get("imageData")
    thumbnail_data = payload.get("thumbnailData")
    if new and bool(image_data) != bool(thumbnail_data):
        raise ValueError("Carga la fotografía principal y la miniatura juntas.")
    if image_data or thumbnail_data:
        if not image_data or not thumbnail_data:
            raise ValueError("Carga la fotografía principal y la miniatura juntas.")
        item["imageData"] = base64.b64encode(gallery_store._decode_jpeg(image_data, "principal", MAX_IMAGE_BYTES, 2000)).decode("ascii")
        item["thumbnailData"] = base64.b64encode(gallery_store._decode_jpeg(thumbnail_data, "miniatura", MAX_THUMBNAIL_BYTES, 600)).decode("ascii")
    return item


def list_services(include_hidden: bool = False) -> list[dict]:
    if _remote():
        result = google_sheets.services_list(include_hidden)
        return sorted(result, key=lambda item: int(item.get("sortOrder", 0)))
    with _session() as db:
        initialized = db.execute("SELECT value FROM service_metadata WHERE key='defaults_initialized'").fetchone()
        if not initialized:
            now = datetime.now(timezone.utc).isoformat()
            db.executemany("INSERT INTO service_items VALUES (?,?,?,?,?,?,?,?,?)", [
                (uuid.uuid4().hex, item["name"], item["description"], json.dumps(item["services"], ensure_ascii=False), 1, index, now, None, None)
                for index, item in enumerate(DEFAULT_SERVICES)
            ])
            db.execute("INSERT INTO service_metadata(key,value) VALUES('defaults_initialized','true')")
        rows = db.execute("SELECT * FROM service_items " + ("" if include_hidden else "WHERE published=1 ") + "ORDER BY sort_order ASC, created_at ASC").fetchall()
    return [_public_item(row) for row in rows]


def create_service(item: dict) -> dict:
    if _remote():
        return google_sheets.services_create(item)
    with _session() as db:
        sort_order = int(db.execute("SELECT COALESCE(MAX(sort_order),-1)+1 FROM service_items").fetchone()[0])
        now = datetime.now(timezone.utc).isoformat()
        image = base64.b64decode(item["imageData"]) if item.get("imageData") else None
        thumbnail = base64.b64decode(item["thumbnailData"]) if item.get("thumbnailData") else None
        db.execute("INSERT INTO service_items VALUES (?,?,?,?,?,?,?,?,?)", (item["id"], item["name"], item["description"], json.dumps(item["services"], ensure_ascii=False), int(item["published"]), sort_order, now, image, thumbnail))
        item.update(sortOrder=sort_order, createdAt=now, imageAvailable=bool(image))
    return item


def update_service(service_id: str, item: dict) -> dict:
    if _remote():
        return google_sheets.services_update(service_id, item)
    with _session() as db:
        row = db.execute("SELECT image,thumbnail,sort_order,created_at FROM service_items WHERE id=?", (service_id,)).fetchone()
        if not row:
            raise ValueError("El servicio ya no existe.")
        image = base64.b64decode(item["imageData"]) if item.get("imageData") else row["image"]
        thumbnail = base64.b64decode(item["thumbnailData"]) if item.get("thumbnailData") else row["thumbnail"]
        db.execute("UPDATE service_items SET name=?,description=?,services=?,published=?,image=?,thumbnail=? WHERE id=?", (item["name"], item["description"], json.dumps(item["services"], ensure_ascii=False), int(item["published"]), image, thumbnail, service_id))
        item.update(id=service_id, sortOrder=row["sort_order"], createdAt=row["created_at"], imageAvailable=bool(image))
    return item


def delete_service(service_id: str) -> None:
    if _remote():
        google_sheets.services_delete(service_id)
        return
    with _session() as db:
        db.execute("DELETE FROM service_items WHERE id=?", (service_id,))


def reorder_services(service_ids: list[str]) -> None:
    if _remote():
        google_sheets.services_reorder(service_ids)
        return
    with _session() as db:
        current = {row[0] for row in db.execute("SELECT id FROM service_items")}
        if len(service_ids) != len(current) or set(service_ids) != current:
            raise ValueError("La lista de servicios cambió. Recarga e inténtalo de nuevo.")
        db.executemany("UPDATE service_items SET sort_order=? WHERE id=?", [(index, service_id) for index, service_id in enumerate(service_ids)])


def get_image(service_id: str, variant: str = "original", include_hidden: bool = False) -> tuple[bytes, str]:
    if variant not in {"original", "thumbnail"}:
        raise ValueError("La variante de imagen no es válida.")
    if _remote():
        result = google_sheets.services_image(service_id, variant, include_hidden)
        try:
            return base64.b64decode(result.get("data", ""), validate=True), str(result.get("mimeType") or "image/jpeg")
        except (ValueError, base64.binascii.Error):
            raise google_sheets.GoogleSheetsError("Drive devolvió una imagen con formato inválido.") from None
    with _session() as db:
        row = db.execute("SELECT image,thumbnail,published FROM service_items WHERE id=?", (service_id,)).fetchone()
    if not row or (not include_hidden and not row["published"]):
        raise ValueError("El servicio no existe.")
    photo = row["thumbnail"] if variant == "thumbnail" else row["image"]
    if not photo:
        raise ValueError("El servicio aún no tiene fotografía.")
    return bytes(photo), "image/jpeg"
