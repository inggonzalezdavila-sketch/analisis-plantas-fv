"""Persistencia y catálogos de las propuestas técnico-comerciales MLY SolarOps."""
from __future__ import annotations

import json
import os
import posixpath
import re
import sqlite3
import uuid
import zipfile
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("QUOTE_DB_PATH", str(ROOT / "data" / "cotizaciones.sqlite3")))
MAIN_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PKG_REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"
CENT = Decimal("0.01")


def _google_mode() -> bool:
    backend = (os.environ.get("QUOTE_STORAGE_BACKEND") or "").strip().casefold()
    if backend == "sqlite":
        return False
    if backend in {"google_sheets", "apps_script"} or os.environ.get("APPS_SCRIPT_EXEC_URL") or os.environ.get("APPS_SCRIPT_TOKEN"):
        return True
    # On Render, never silently fall back to its temporary filesystem.
    return bool(os.environ.get("RENDER"))


@contextmanager
def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH, timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialise() -> None:
    with connect() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS quote_settings (
                key TEXT PRIMARY KEY, value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS quote_clients (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS quote_products (
                code TEXT PRIMARY KEY, name TEXT NOT NULL, payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS quotes (
                id TEXT PRIMARY KEY, number TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL,
                quote_date TEXT NOT NULL, client_name TEXT NOT NULL, project TEXT NOT NULL,
                total TEXT NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS quotes_created_at_idx ON quotes(created_at DESC);
        """)
        db.execute("INSERT OR IGNORE INTO quote_settings(key,value) VALUES('sequence','0')")


def _text(value: str | None) -> str:
    return (value or "").strip()


def _normal(value: str) -> str:
    import unicodedata
    value = unicodedata.normalize("NFKD", value.lower())
    return "".join(ch for ch in value if ch.isalnum())


def _column_index(reference: str) -> int:
    match = re.match(r"[A-Z]+", reference or "")
    if not match:
        return 0
    index = 0
    for char in match.group(0):
        index = index * 26 + ord(char) - 64
    return index - 1


def _xlsx_sheets(raw: bytes) -> dict[str, list[dict[int, str]]]:
    with zipfile.ZipFile(__import__("io").BytesIO(raw)) as book:
        strings = []
        try:
            root = ET.fromstring(book.read("xl/sharedStrings.xml"))
            strings = ["".join(item.itertext()).strip() for item in root.findall(MAIN_NS + "si")]
        except KeyError:
            pass
        workbook = ET.fromstring(book.read("xl/workbook.xml"))
        rels = ET.fromstring(book.read("xl/_rels/workbook.xml.rels"))
        targets = {item.get("Id"): item.get("Target", "") for item in rels.findall(PKG_REL_NS + "Relationship")}
        sheets: dict[str, list[dict[int, str]]] = {}
        for sheet in workbook.findall(f"{MAIN_NS}sheets/{MAIN_NS}sheet"):
            name = sheet.get("name", "")
            target = targets.get(sheet.get(REL_NS + "id", ""), "")
            if not name or not target:
                continue
            path = posixpath.normpath(target.lstrip("/") if target.startswith("/") else posixpath.join("xl", target))
            if path not in book.namelist():
                continue
            root = ET.fromstring(book.read(path))
            rows = []
            for row in root.findall(f"{MAIN_NS}sheetData/{MAIN_NS}row"):
                cells: dict[int, str] = {}
                for cell in row.findall(MAIN_NS + "c"):
                    cell_type = cell.get("t", "")
                    if cell_type == "inlineStr":
                        value = "".join(cell.itertext()).strip()
                    else:
                        value = _text(cell.findtext(MAIN_NS + "v"))
                        if cell_type == "s" and value:
                            try:
                                value = strings[int(value)]
                            except (ValueError, IndexError):
                                value = ""
                    cells[_column_index(cell.get("r", ""))] = value
                rows.append(cells)
            sheets[name] = rows
        return sheets


def _find_header(rows: list[dict[int, str]], required: set[str]) -> tuple[int, dict[str, int]]:
    for row_index, row in enumerate(rows):
        headers = {_normal(value): column for column, value in row.items() if _text(value)}
        if required.issubset(headers):
            return row_index, headers
    raise ValueError("La plantilla no contiene los encabezados esperados de clientes y productos.")


def _field(row: dict[int, str], headers: dict[str, int], *aliases: str) -> str:
    for alias in aliases:
        index = headers.get(_normal(alias))
        if index is not None:
            return _text(row.get(index))
    return ""


def _active(row: dict[int, str], headers: dict[str, int]) -> bool:
    value = _field(row, headers, "Activo", "Activa", "Estado").casefold()
    return value not in {"no", "n", "false", "falso", "0", "inactivo", "inactiva"}


def import_template(raw: bytes) -> dict:
    if _google_mode():
        raise ValueError("La plantilla y el registro deben administrarse en la hoja de Google Drive conectada.")
    if len(raw) > 15 * 1024 * 1024:
        raise ValueError("La plantilla supera el límite de 15 MB.")
    sheets = _xlsx_sheets(raw)
    if "Clientes" not in sheets or "Productos_Servicios" not in sheets or "Configuracion" not in sheets:
        raise ValueError("Carga la plantilla MLY Solar con las hojas Clientes, Productos_Servicios y Configuracion.")

    clients_rows = sheets["Clientes"]
    client_start, client_headers = _find_header(clients_rows, {_normal("ID Cliente"), _normal("Nombre / Razón social")})
    clients = []
    for row in clients_rows[client_start + 1:]:
        client_id = _field(row, client_headers, "ID Cliente")
        name = _field(row, client_headers, "Nombre / Razón social", "Nombre")
        if not name or not client_id or not _active(row, client_headers):
            continue
        clients.append({
            "id": client_id, "name": name,
            "documentType": _field(row, client_headers, "Tipo doc.", "Tipo documento"),
            "document": _field(row, client_headers, "Documento / NIT", "Documento", "NIT"),
            "dv": _field(row, client_headers, "DV"),
            "phone": _field(row, client_headers, "Teléfono", "Telefono"),
            "email": _field(row, client_headers, "Correo", "Email"),
            "city": _field(row, client_headers, "Ciudad"),
            "address": _field(row, client_headers, "Dirección", "Direccion"),
            "type": _field(row, client_headers, "Tipo cliente"),
        })

    product_rows = sheets["Productos_Servicios"]
    product_start, product_headers = _find_header(product_rows, {_normal("Código"), _normal("Descripción")})
    products = []
    for row in product_rows[product_start + 1:]:
        code = _field(row, product_headers, "Código", "Codigo")
        name = _field(row, product_headers, "Descripción", "Descripcion")
        if not code or not name or not _active(row, product_headers):
            continue
        products.append({
            "code": code, "type": _field(row, product_headers, "Tipo"), "name": name,
            "brand": _field(row, product_headers, "Marca / Modelo", "Marca/Modelo"),
            "unit": _field(row, product_headers, "Unidad", "UND."),
            "price": _field(row, product_headers, "Precio venta", "Precio de venta"),
            "ivaRate": _field(row, product_headers, "IVA", "Tasa IVA"),
        })

    config: dict[str, str] = {}
    for row in sheets["Configuracion"]:
        key, value = _text(row.get(0)), _text(row.get(1))
        if key and value:
            config[key] = value
    options = {
        "systemTypes": [_text(row.get(3)) for row in sheets["Configuracion"] if _text(row.get(3)) and _normal(_text(row.get(3))) != _normal("TIPOS SISTEMA")],
        "documentTypes": [_text(row.get(5)) for row in sheets["Configuracion"] if _text(row.get(5)) and _normal(_text(row.get(5))) != _normal("TIPOS DOCUMENTO")],
        "statuses": [_text(row.get(6)) for row in sheets["Configuracion"] if _text(row.get(6)) and _normal(_text(row.get(6))) != _normal("ESTADOS COTIZACIÓN")],
        "ivaRates": [],
    }
    for row in sheets["Configuracion"]:
        try:
            rate = Decimal(_text(row.get(7)))
        except InvalidOperation:
            continue
        if Decimal("0") <= rate <= Decimal("1") and rate not in options["ivaRates"]:
            options["ivaRates"].append(rate)
    options["ivaRates"].sort()

    defaults = {
        "advisor": config.get("Asesor por defecto", ""),
        "validityDays": config.get("Validez por defecto (días)", "15"),
        "payment": config.get("Forma de pago por defecto", ""),
        "delivery": config.get("Tiempo de entrega por defecto", ""),
        "warranty": config.get("Garantía por defecto", ""),
        "transport": config.get("Transporte por defecto", ""),
        "scope": "", "included": "", "excluded": "", "notes": "",
    }
    quote_rows = sheets.get("Cotizacion", [])
    for index, row in enumerate(quote_rows):
        label = _normal(_text(row.get(0)))
        if label == _normal("ALCANCE DEL SERVICIO / PROPUESTA"):
            defaults["scope"] = next((_text(quote_rows[pos].get(0)) for pos in range(index + 1, min(index + 4, len(quote_rows))) if _text(quote_rows[pos].get(0))), "")
        elif label == _normal("ALCANCE INCLUIDO") and index + 1 < len(quote_rows):
            defaults["included"] = _text(quote_rows[index + 1].get(0))
            defaults["excluded"] = _text(quote_rows[index + 1].get(5))
        elif label == _normal("OBSERVACIONES ESTÁNDAR RECOMENDADAS") and index + 1 < len(quote_rows):
            defaults["notes"] = _text(quote_rows[index + 1].get(0))

    max_number = 0
    for row in sheets.get("Cotizacion", []):
        for value in row.values():
            match = re.search(r"COT[- ]?0*(\d+)", value, re.IGNORECASE)
            if match:
                max_number = max(max_number, int(match.group(1)))
    for row in sheets.get("Historial_Cotizaciones", [])[7:]:
        value = _text(row.get(0))
        match = re.fullmatch(r"(?:COT[- ]?)?0*(\d+)", value, re.IGNORECASE)
        if match:
            max_number = max(max_number, int(match.group(1)))

    if not clients or not products:
        raise ValueError("No se encontraron clientes y productos activos en la plantilla.")
    initialise()
    with connect() as db:
        db.execute("DELETE FROM quote_clients")
        db.executemany("INSERT INTO quote_clients(id,name,payload) VALUES(?,?,?)", [(item["id"], item["name"], json.dumps(item, ensure_ascii=False)) for item in clients])
        db.execute("DELETE FROM quote_products")
        db.executemany("INSERT INTO quote_products(code,name,payload) VALUES(?,?,?)", [(item["code"], item["name"], json.dumps(item, ensure_ascii=False)) for item in products])
        db.execute("INSERT OR REPLACE INTO quote_settings(key,value) VALUES('company',?)", (json.dumps(config, ensure_ascii=False),))
        db.execute("INSERT OR REPLACE INTO quote_settings(key,value) VALUES('defaults',?)", (json.dumps(defaults, ensure_ascii=False),))
        db.execute("INSERT OR REPLACE INTO quote_settings(key,value) VALUES('options',?)", (json.dumps(options, ensure_ascii=False, default=str),))
        db.execute("INSERT OR REPLACE INTO quote_settings(key,value) VALUES('template_loaded_at',?)", (datetime.now(timezone.utc).isoformat(),))
        current = int(db.execute("SELECT value FROM quote_settings WHERE key='sequence'").fetchone()[0])
        sequence = max(current, max_number)
        db.execute("UPDATE quote_settings SET value=? WHERE key='sequence'", (str(sequence),))
    return {"clients": len(clients), "products": len(products), "company": config.get("Razón social", "MLY SOLAR O&M S.A.S."), "nextQuote": f"COT-{sequence + 1:04d}"}


def bootstrap() -> dict:
    if _google_mode():
        import google_sheets
        return google_sheets.bootstrap()
    initialise()
    with connect() as db:
        settings = {row["key"]: row["value"] for row in db.execute("SELECT key,value FROM quote_settings")}
        clients = [json.loads(row["payload"]) for row in db.execute("SELECT payload FROM quote_clients ORDER BY name COLLATE NOCASE")]
        products = [json.loads(row["payload"]) for row in db.execute("SELECT payload FROM quote_products ORDER BY name COLLATE NOCASE")]
        quotes = [
            {"id": row["id"], "number": row["number"], "createdAt": row["created_at"], "date": row["quote_date"], "client": row["client_name"], "project": row["project"], "total": row["total"], "status": row["status"]}
            for row in db.execute("SELECT * FROM quotes ORDER BY created_at DESC LIMIT 200")
        ]
    company = json.loads(settings.get("company", "{}"))
    defaults = json.loads(settings.get("defaults", "{}"))
    options = json.loads(settings.get("options", "{}"))
    return {"ready": bool(settings.get("template_loaded_at")), "templateLoadedAt": settings.get("template_loaded_at"), "sequence": int(settings.get("sequence", "0")) + 1, "company": company, "defaults": defaults, "options": options, "clients": clients, "products": products, "quotes": quotes}


def _decimal(value: object, label: str, *, maximum: Decimal = Decimal("1000000000000")) -> Decimal:
    try:
        amount = Decimal(str(value if value not in (None, "") else "0"))
    except (InvalidOperation, ValueError):
        raise ValueError(f"El campo {label} debe ser numérico.") from None
    if not amount.is_finite() or amount < 0 or amount > maximum:
        raise ValueError(f"El campo {label} tiene un valor fuera del rango permitido.")
    return amount


def _money(value: Decimal) -> str:
    return str(value.quantize(CENT, rounding=ROUND_HALF_UP))


def _quote_record(sequence: int, quote_date: str, parsed_date: date, validity: int, client_fields: dict, project: dict, system: dict, conditions: dict, cleaned_items: list[dict], gross: Decimal, discounts: Decimal, iva_total: Decimal, aiu: Decimal, total_without_iva: Decimal, total: Decimal, status: str, created_by: str) -> dict:
    created_at = datetime.now(timezone.utc).isoformat()
    return {
        "id": uuid.uuid4().hex, "number": f"COT-{sequence:04d}", "createdAt": created_at, "createdBy": created_by,
        "date": quote_date, "validityDays": validity, "validUntil": (parsed_date + timedelta(days=validity)).isoformat(),
        "client": client_fields, "project": project, "system": system, "conditions": conditions,
        "items": cleaned_items, "totals": {
            "subtotal": _money(gross), "discounts": _money(discounts), "iva": _money(iva_total),
            "aiu": _money(aiu), "totalWithoutIva": _money(total_without_iva), "total": _money(total),
        }, "status": status,
    }


def create_quote(payload: dict, created_by: str) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("La cotización no tiene un formato válido.")
    client = payload.get("client") or {}
    if not isinstance(client, dict):
        raise ValueError("Los datos del cliente no tienen un formato válido.")
    if not _text(client.get("name")):
        raise ValueError("Indica el nombre o razón social del cliente.")
    items = payload.get("items")
    if not isinstance(items, list) or not 1 <= len(items) <= 100:
        raise ValueError("Agrega entre 1 y 100 equipos o actividades.")
    quote_date = _text(payload.get("date")) or date.today().isoformat()
    try:
        parsed_date = date.fromisoformat(quote_date)
    except ValueError:
        raise ValueError("La fecha de cotización no es válida.") from None
    try:
        validity = int(payload.get("validityDays", 5))
    except (TypeError, ValueError):
        raise ValueError("La vigencia debe ser un número de días.") from None
    if not 1 <= validity <= 365:
        raise ValueError("La vigencia debe estar entre 1 y 365 días.")

    cleaned_items = []
    gross = Decimal("0")
    discounts = Decimal("0")
    iva_total = Decimal("0")
    for index, raw in enumerate(items, 1):
        if not isinstance(raw, dict) or not _text(raw.get("description")):
            raise ValueError(f"El ítem {index} necesita una descripción.")
        quantity = _decimal(raw.get("quantity", 1), f"cantidad del ítem {index}", maximum=Decimal("1000000"))
        price = _decimal(raw.get("unitPrice", 0), f"precio del ítem {index}")
        discount = _decimal(raw.get("discount", 0), f"descuento del ítem {index}")
        rate = _decimal(raw.get("ivaRate", 0), f"IVA del ítem {index}", maximum=Decimal("1"))
        if quantity <= 0:
            raise ValueError(f"La cantidad del ítem {index} debe ser mayor que cero.")
        line_gross = (quantity * price).quantize(CENT, rounding=ROUND_HALF_UP)
        if discount > line_gross:
            raise ValueError(f"El descuento del ítem {index} supera el valor de la línea.")
        base = line_gross - discount
        iva = (base * rate).quantize(CENT, rounding=ROUND_HALF_UP)
        line_total = base + iva
        gross += line_gross
        discounts += discount
        iva_total += iva
        cleaned_items.append({
            "line": index, "code": _text(raw.get("code"))[:80], "description": _text(raw.get("description"))[:500],
            "brand": _text(raw.get("brand"))[:160], "unit": _text(raw.get("unit"))[:40],
            "quantity": str(quantity), "unitPrice": _money(price), "discount": _money(discount),
            "ivaRate": str(rate), "base": _money(base), "iva": _money(iva), "total": _money(line_total),
        })
    aiu = _decimal(payload.get("aiu", 0), "AIU")
    subtotal_after_discount = gross - discounts
    total = subtotal_after_discount + iva_total + aiu
    total_without_iva = subtotal_after_discount + aiu
    status = _text(payload.get("status")) or "Borrador"
    if status not in {"Borrador", "Enviada", "Aprobada", "Rechazada", "Vencida"}:
        raise ValueError("Selecciona un estado válido para la cotización.")

    client_fields = {key: _text(client.get(key))[:500] for key in ("id", "name", "documentType", "document", "dv", "phone", "email", "city", "address")}
    system = payload.get("system") if isinstance(payload.get("system"), dict) else {}
    project = payload.get("project") if isinstance(payload.get("project"), dict) else {}
    conditions = payload.get("conditions") if isinstance(payload.get("conditions"), dict) else {}
    for group in (system, project, conditions):
        for key, value in list(group.items()):
            group[key] = _text(str(value))[:3000]
    if _google_mode():
        import google_sheets
        with google_sheets.WRITE_LOCK:
            remote = google_sheets.bootstrap()
            if not remote["ready"]:
                raise ValueError("La hoja de Google Drive no tiene clientes y productos activos en la plantilla MLY.")
            sequence = int(remote["sequence"])
            full = _quote_record(sequence, quote_date, parsed_date, validity, client_fields, project, system, conditions, cleaned_items, gross, discounts, iva_total, aiu, total_without_iva, total, status, created_by)
            google_sheets.append_quote(full)
            return full

    initialise()
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        settings = db.execute("SELECT value FROM quote_settings WHERE key='template_loaded_at'").fetchone()
        if not settings:
            raise ValueError("Carga primero tu plantilla MLY de cotizaciones para usar los clientes y productos.")
        sequence = int(db.execute("SELECT value FROM quote_settings WHERE key='sequence'").fetchone()[0]) + 1
        number = f"COT-{sequence:04d}"
        full = _quote_record(sequence, quote_date, parsed_date, validity, client_fields, project, system, conditions, cleaned_items, gross, discounts, iva_total, aiu, total_without_iva, total, status, created_by)
        quote_id = full["id"]
        created_at = full["createdAt"]
        db.execute("INSERT INTO quotes(id,number,created_at,quote_date,client_name,project,total,status,payload) VALUES(?,?,?,?,?,?,?,?,?)", (
            quote_id, number, created_at, quote_date, client_fields["name"], project.get("location", ""), _money(total), status, json.dumps(full, ensure_ascii=False),
        ))
        db.execute("UPDATE quote_settings SET value=? WHERE key='sequence'", (str(sequence),))
    return full


def get_quote(quote_id: str) -> dict | None:
    if _google_mode():
        import google_sheets
        return google_sheets.get_quote(quote_id)
    initialise()
    with connect() as db:
        row = db.execute("SELECT payload FROM quotes WHERE id=?", (quote_id,)).fetchone()
    return json.loads(row["payload"]) if row else None
