"""Puente privado a Google Sheets mediante un Apps Script del propietario."""
from __future__ import annotations

import json
import os
import re
import threading
import unicodedata
from decimal import Decimal, InvalidOperation
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

SCRIPT_URL = (os.environ.get("APPS_SCRIPT_EXEC_URL") or "").strip()
SCRIPT_TOKEN = (os.environ.get("APPS_SCRIPT_TOKEN") or "").strip()
WRITE_LOCK = threading.RLock()


class GoogleSheetsError(Exception):
    """Error seguro al comunicarse con la hoja vinculada a Apps Script."""


def configured() -> bool:
    parsed = urlparse(SCRIPT_URL)
    return bool(
        parsed.scheme == "https"
        and parsed.hostname == "script.google.com"
        and parsed.path.startswith("/macros/s/")
        and parsed.path.endswith("/exec")
        and SCRIPT_TOKEN
    )


def _call(action: str, **payload) -> dict:
    if not configured():
        raise GoogleSheetsError("Falta configurar en Render la URL de Apps Script y su clave privada.")
    body = json.dumps({"action": action, "token": SCRIPT_TOKEN, **payload}, ensure_ascii=False).encode("utf-8")
    request = Request(SCRIPT_URL, data=body, headers={"Accept": "application/json", "Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(request, timeout=35) as response:
            result = json.loads(response.read(8_000_000).decode("utf-8"))
    except HTTPError as error:
        if error.code in {401, 403}:
            raise GoogleSheetsError("Google no autorizó el Apps Script. Revisa el despliegue y la clave privada.") from None
        raise GoogleSheetsError("Apps Script no pudo completar la operación. Revisa que el despliegue siga activo.") from None
    except (URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise GoogleSheetsError("No fue posible comunicarse con Google Drive mediante Apps Script.") from None
    if not isinstance(result, dict):
        raise GoogleSheetsError("Apps Script devolvió una respuesta inesperada.")
    if result.get("error"):
        message = str(result["error"])[:300]
        raise GoogleSheetsError(message)
    return result


def _values(range_name: str) -> list[list]:
    result = _call("get_range", range=range_name)
    values = result.get("values", [])
    return values if isinstance(values, list) else []


def _normal(value: object) -> str:
    decomposed = unicodedata.normalize("NFKD", str(value or "").lower())
    return "".join(character for character in decomposed if character.isalnum())


def _records(values: list[list]) -> tuple[dict[str, int], list[dict[str, object]]]:
    if not values:
        return {}, []
    headers = {_normal(value): index for index, value in enumerate(values[0]) if value not in (None, "")}
    rows = []
    for row in values[1:]:
        rows.append({key: row[index] if index < len(row) else "" for key, index in headers.items()})
    return headers, rows


def _pick(row: dict[str, object], *names: str) -> str:
    for name in names:
        value = row.get(_normal(name))
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _enabled(row: dict[str, object]) -> bool:
    return _pick(row, "Activo", "Activa", "Estado").casefold() not in {"no", "n", "false", "falso", "0", "inactivo", "inactiva"}


def _num(value: object) -> float:
    try:
        number = Decimal(str(value if value not in (None, "") else "0"))
        return float(number) if number.is_finite() else 0.0
    except (InvalidOperation, ValueError):
        return 0.0


def _mapping(values: list[list]) -> dict[str, str]:
    return {str(row[0]).strip(): str(row[1]).strip() for row in values[1:] if len(row) > 1 and row[0] not in (None, "") and row[1] not in (None, "")}


def _quote_number(value: object) -> int:
    match = re.search(r"COT[- ]?0*(\d+)", str(value or ""), re.IGNORECASE)
    if match:
        return int(match.group(1))
    try:
        number = Decimal(str(value or ""))
        return int(number) if number.is_finite() and number >= 0 and number == number.to_integral_value() else 0
    except (InvalidOperation, ValueError):
        return 0


def _sheet_names() -> set[str]:
    result = _call("list_sheets")
    names = result.get("sheets", [])
    return {str(name) for name in names} if isinstance(names, list) else set()


def _company_and_defaults() -> tuple[dict, dict, dict]:
    company = _mapping(_values("Configuracion!A3:B15"))
    config_rows = _values("Configuracion!A3:H")
    systems, documents, statuses, rates = [], [], [], []
    for row in config_rows[1:]:
        if len(row) > 3 and row[3] not in (None, ""):
            systems.append(str(row[3]))
        if len(row) > 5 and row[5] not in (None, ""):
            documents.append(str(row[5]))
        if len(row) > 6 and row[6] not in (None, ""):
            statuses.append(str(row[6]))
        if len(row) > 7 and row[7] not in (None, ""):
            rate = _num(row[7])
            if 0 <= rate <= 1 and rate not in rates:
                rates.append(rate)
    quotation = _values("Cotizacion!A1:K68")
    defaults = {
        "advisor": company.get("Asesor por defecto", ""),
        "validityDays": company.get("Validez por defecto (días)", "15"),
        "payment": company.get("Forma de pago por defecto", ""),
        "delivery": company.get("Tiempo de entrega por defecto", ""),
        "warranty": company.get("Garantía por defecto", ""),
        "transport": company.get("Transporte por defecto", ""),
        "scope": "", "included": "", "excluded": "", "notes": "",
    }
    for index, row in enumerate(quotation):
        label = _normal(row[0] if row else "")
        if label == _normal("ALCANCE DEL SERVICIO / PROPUESTA") and index + 1 < len(quotation):
            defaults["scope"] = next((str(next_row[0]) for next_row in quotation[index + 1:index + 4] if next_row and next_row[0]), "")
        elif label == _normal("ALCANCE INCLUIDO") and index + 1 < len(quotation):
            next_row = quotation[index + 1]
            defaults["included"] = str(next_row[0]) if next_row and next_row[0] else ""
            defaults["excluded"] = str(next_row[5]) if len(next_row) > 5 and next_row[5] else ""
        elif label == _normal("OBSERVACIONES ESTÁNDAR RECOMENDADAS") and index + 1 < len(quotation):
            next_row = quotation[index + 1]
            defaults["notes"] = str(next_row[0]) if next_row and next_row[0] else ""
    return company, defaults, {"systemTypes": systems, "documentTypes": documents, "statuses": statuses, "ivaRates": rates}


def _client_rows() -> list[dict]:
    _, rows = _records(_values("Clientes!A5:L"))
    clients = []
    for row in rows:
        client_id = _pick(row, "ID Cliente")
        name = _pick(row, "Nombre / Razón social")
        if not client_id or not name or not _enabled(row):
            continue
        clients.append({"id": client_id, "name": name, "documentType": _pick(row, "Tipo doc."), "document": _pick(row, "Documento / NIT"), "dv": _pick(row, "DV"), "phone": _pick(row, "Teléfono"), "email": _pick(row, "Correo"), "city": _pick(row, "Ciudad"), "address": _pick(row, "Dirección"), "type": _pick(row, "Tipo cliente")})
    return clients


def _product_rows() -> list[dict]:
    _, rows = _records(_values("Productos_Servicios!A5:K"))
    products = []
    for row in rows:
        code, name = _pick(row, "Código"), _pick(row, "Descripción")
        if not code or not name or not _enabled(row):
            continue
        products.append({"code": code, "type": _pick(row, "Tipo"), "name": name, "brand": _pick(row, "Marca / Modelo"), "unit": _pick(row, "Unidad"), "price": _pick(row, "Precio venta"), "ivaRate": _pick(row, "Tasa IVA")})
    return products


def add_product(product: dict) -> dict:
    result = _call("add_product", product=product)
    saved = result.get("product")
    return saved if isinstance(saved, dict) else product


def bootstrap() -> dict:
    required = {"Clientes", "Productos_Servicios", "Configuracion", "Cotizacion", "Registro_Cotizaciones", "Detalle_Cotizaciones"}
    missing = sorted(required - _sheet_names())
    if missing:
        raise GoogleSheetsError("La hoja de Drive no tiene las pestañas requeridas: " + ", ".join(missing) + ".")
    company, defaults, options = _company_and_defaults()
    _, registry = _records(_values("Registro_Cotizaciones!A4:AO"))
    quote_number = _values("Cotizacion!J1")
    max_sequence = _quote_number(quote_number[0][0] if quote_number and quote_number[0] else "")
    for row in _values("Historial_Cotizaciones!A8:A207"):
        if row:
            max_sequence = max(max_sequence, _quote_number(row[0]))
    quotes = []
    for row in registry:
        quote_id = _pick(row, "ID cotización")
        number = _pick(row, "Número cotización")
        if not quote_id or not number:
            continue
        max_sequence = max(max_sequence, _quote_number(number))
        quotes.append({"id": quote_id, "number": number, "createdAt": _pick(row, "Fecha creación"), "date": _pick(row, "Fecha cotización"), "client": _pick(row, "Cliente / razón social"), "project": _pick(row, "Proyecto / ubicación"), "total": _pick(row, "Total cotizado (COP)"), "status": _pick(row, "Estado") or "Borrador"})
    clients, products = _client_rows(), _product_rows()
    return {"ready": bool(clients and products), "storage": "google_sheets", "templateLoadedAt": "Google Drive", "sequence": max_sequence + 1, "company": company, "defaults": defaults, "options": options, "clients": clients, "products": products, "quotes": quotes[-200:][::-1]}


def _quote_rows(quote: dict) -> tuple[list, list[list]]:
    client, project, system, conditions, totals = quote["client"], quote["project"], quote["system"], quote["conditions"], quote["totals"]
    registry_row = [quote["id"], quote["number"], quote["createdAt"], quote["date"], quote["validityDays"], quote["validUntil"], client["id"], client["name"], client["documentType"], client["document"], client["dv"], client["phone"], client["email"], client["city"], client["address"], project.get("location", ""), project.get("systemType", ""), project.get("advisor", ""), conditions.get("scope", ""), conditions.get("payment", ""), conditions.get("delivery", ""), conditions.get("warranty", ""), conditions.get("transport", ""), conditions.get("included", ""), conditions.get("excluded", ""), conditions.get("notes", ""), system.get("pvKwp", ""), system.get("panelW", ""), system.get("panels", ""), system.get("inverterKw", ""), system.get("batteryKwh", ""), system.get("generationKwhMonth", ""), system.get("consumptionKwhMonth", ""), system.get("coveragePercent", ""), totals["subtotal"], totals["discounts"], totals["iva"], totals["aiu"], totals["totalWithoutIva"], totals["total"], quote["status"]]
    numeric_registry = {4, *range(26, 40)}
    registry_row = [_num(value) if index in numeric_registry and value not in (None, "") else value for index, value in enumerate(registry_row)]
    detail_rows = []
    for item in quote["items"]:
        values = [quote["id"], quote["number"], item["line"], item["code"], item["description"], item["brand"], item["unit"], item["quantity"], item["unitPrice"], item["discount"], item["base"], item["ivaRate"], item["iva"], item["total"]]
        numeric_detail = {2, *range(7, 14)}
        detail_rows.append([_num(value) if index in numeric_detail and value not in (None, "") else value for index, value in enumerate(values)])
    return registry_row, detail_rows


def append_quote(quote: dict) -> None:
    registry_row, detail_rows = _quote_rows(quote)
    _call("append_quote", registry_row=registry_row, detail_rows=detail_rows)


def update_quote(quote: dict) -> None:
    registry_row, detail_rows = _quote_rows(quote)
    _call("update_quote", registry_row=registry_row, detail_rows=detail_rows)


def get_quote(quote_id: str) -> dict | None:
    _, registry = _records(_values("Registro_Cotizaciones!A4:AO"))
    row = next((item for item in registry if _pick(item, "ID cotización") == quote_id), None)
    if not row:
        return None
    _, details = _records(_values("Detalle_Cotizaciones!A4:N"))
    items = []
    for item in details:
        if _pick(item, "ID cotización") != quote_id:
            continue
        items.append({"line": int(_num(_pick(item, "Ítem"))), "code": _pick(item, "Código producto/servicio"), "description": _pick(item, "Descripción"), "brand": _pick(item, "Marca / modelo"), "unit": _pick(item, "Unidad"), "quantity": _pick(item, "Cantidad"), "unitPrice": _pick(item, "Precio unitario (COP)"), "discount": _pick(item, "Descuento (COP)"), "base": _pick(item, "Base gravable (COP)"), "ivaRate": _pick(item, "Tasa IVA"), "iva": _pick(item, "IVA de línea (COP)"), "total": _pick(item, "Total de línea (COP)")})
    company, _, _ = _company_and_defaults()
    return {
        "id": quote_id, "number": _pick(row, "Número cotización"), "createdAt": _pick(row, "Fecha creación"), "date": _pick(row, "Fecha cotización"), "validityDays": int(_num(_pick(row, "Días de validez"))), "validUntil": _pick(row, "Fecha vencimiento"), "createdBy": "",
        "client": {"id": _pick(row, "ID cliente"), "name": _pick(row, "Cliente / razón social"), "documentType": _pick(row, "Tipo documento"), "document": _pick(row, "Documento / NIT"), "dv": _pick(row, "DV"), "phone": _pick(row, "Teléfono"), "email": _pick(row, "Correo"), "city": _pick(row, "Ciudad"), "address": _pick(row, "Dirección")},
        "project": {"location": _pick(row, "Proyecto / ubicación"), "systemType": _pick(row, "Tipo de sistema"), "advisor": _pick(row, "Asesor")},
        "system": {"pvKwp": _pick(row, "Potencia FV (kWp)"), "panelW": _pick(row, "Potencia panel (W)"), "panels": _pick(row, "Número de paneles"), "inverterKw": _pick(row, "Potencia inversor (kW)"), "batteryKwh": _pick(row, "Capacidad batería (kWh)"), "generationKwhMonth": _pick(row, "Generación estimada (kWh/mes)"), "consumptionKwhMonth": _pick(row, "Consumo estimado (kWh/mes)"), "coveragePercent": _pick(row, "Cobertura estimada (%)")},
        "conditions": {"scope": _pick(row, "Alcance / propuesta"), "payment": _pick(row, "Forma de pago"), "delivery": _pick(row, "Tiempo de entrega"), "warranty": _pick(row, "Garantía"), "transport": _pick(row, "Transporte"), "included": _pick(row, "Alcance incluido"), "excluded": _pick(row, "No incluye"), "notes": _pick(row, "Observaciones")},
        "items": items, "totals": {"subtotal": _pick(row, "Subtotal antes de impuestos (COP)"), "discounts": _pick(row, "Descuentos (COP)"), "iva": _pick(row, "IVA (COP)"), "aiu": _pick(row, "AIU (COP)"), "totalWithoutIva": _pick(row, "Total sin IVA (COP)"), "total": _pick(row, "Total cotizado (COP)")}, "status": _pick(row, "Estado") or "Borrador", "company": company,
    }
