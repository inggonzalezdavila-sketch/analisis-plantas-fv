"""Servidor local para Análisis de Plantas FV.

Ejecutar: python app.py
Abrir: http://127.0.0.1:8000
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import ssl
import sys
import tempfile
import threading
import time
import unicodedata
import uuid
import zipfile
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from http import HTTPStatus
from http.cookiejar import CookieJar
from http.cookies import SimpleCookie
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import HTTPCookieProcessor, HTTPSHandler, Request, build_opener
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
STATIC_DIR = ROOT / "static"
DATA_DIR.mkdir(exist_ok=True)
NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
DATA_LOCK = threading.RLock()
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_MB", "50")) * 1024 * 1024
SESSION_COOKIE = "fv_session"
SESSION_TTL_SECONDS = 12 * 60 * 60
LOGIN_WINDOW_SECONDS = 15 * 60
MAX_LOGIN_ATTEMPTS = 5
LOGIN_ATTEMPTS: dict[str, list[float]] = defaultdict(list)
LOGIN_LOCK = threading.Lock()
FUSIONSOLAR_CACHE_SECONDS = 5 * 60
FUSIONSOLAR_CACHE: dict[str, object] = {"expires": 0.0, "plants": None, "overviewExpires": 0.0, "overview": None, "reports": {}}
FUSIONSOLAR_LOCK = threading.Lock()
FUSIONSOLAR_USER_AGENT = "Mozilla/5.0 (compatible; AnalisisPlantasFV/1.0; read-only)"


class FusionSolarError(Exception):
    """Error seguro: nunca devuelve credenciales, tokens ni respuestas externas."""


def fusionsolar_configuration() -> tuple[str, str, str]:
    """Obtiene la configuración exclusivamente desde secretos del servidor."""
    base_url = os.environ.get("FUSIONSOLAR_BASE_URL", "").rstrip("/")
    username = os.environ.get("FUSIONSOLAR_USERNAME", "")
    system_code = os.environ.get("FUSIONSOLAR_SYSTEM_CODE", "")
    parsed = urlparse(base_url)
    trusted_host = parsed.hostname and parsed.hostname.endswith(".fusionsolar.huawei.com")
    if parsed.scheme != "https" or not trusted_host or not username or not system_code:
        raise FusionSolarError("La conexión FusionSolar aún no está configurada de forma segura en Render.")
    return base_url, username, system_code


def fusionsolar_post(opener, url: str, payload: dict, token: str | None = None):
    headers = {"Content-Type": "application/json", "Accept": "application/json", "User-Agent": FUSIONSOLAR_USER_AGENT}
    if token:
        headers["XSRF-TOKEN"] = token
    request = Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    try:
        with opener.open(request, timeout=12) as response:
            raw = response.read(1_000_000)
            try:
                body = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise FusionSolarError("FusionSolar devolvió una respuesta no reconocida. Intenta nuevamente.")
            return body, response.headers
    except HTTPError as error:
        if error.code in {401, 402, 403}:
            raise FusionSolarError("FusionSolar rechazó el acceso. Verifica que la cuenta API esté activa y autorizada para las plantas.")
        if error.code == 407:
            raise FusionSolarError("FusionSolar limitó temporalmente las consultas. Espera unos minutos e intenta de nuevo.")
        raise FusionSolarError("FusionSolar no pudo completar la consulta en este momento.")
    except URLError as error:
        reason = str(error.reason).lower()
        if "certificate" in reason or "ssl" in reason:
            raise FusionSolarError("No se pudo establecer una conexión TLS válida con FusionSolar. Intenta de nuevo o revisa la región configurada.")
        if "timed out" in reason or "timeout" in reason:
            raise FusionSolarError("FusionSolar tardó demasiado en responder. Intenta de nuevo en unos minutos.")
        raise FusionSolarError("FusionSolar cerró la conexión. Verifica que la cuenta API esté activa y que se use la región correcta.")
    except (TimeoutError, OSError):
        raise FusionSolarError("No fue posible comunicarse con FusionSolar. Verifica la conexión e intenta nuevamente.")


def prepare_fusionsolar_session(opener, base_url: str) -> None:
    """Recoge las cookies públicas del portal antes del inicio de sesión de API."""
    request = Request(base_url + "/", headers={"User-Agent": FUSIONSOLAR_USER_AGENT, "Accept": "text/html"})
    try:
        with opener.open(request, timeout=12):
            pass
    except (HTTPError, URLError, TimeoutError, OSError):
        # El login de API sigue siendo la comprobación definitiva y entrega un error seguro.
        pass


def fusionsolar_authenticated_client():
    """Abre una sesión temporal de API para consultas de solo lectura."""
    base_url, username, system_code = fusionsolar_configuration()
    cookies = CookieJar()
    tls_context = ssl.create_default_context()
    tls_context.minimum_version = ssl.TLSVersion.TLSv1_2
    tls_context.maximum_version = ssl.TLSVersion.TLSv1_2
    opener = build_opener(HTTPCookieProcessor(cookies), HTTPSHandler(context=tls_context))
    prepare_fusionsolar_session(opener, base_url)
    login, login_headers = fusionsolar_post(
        opener, f"{base_url}/thirdData/login", {"userName": username, "systemCode": system_code}
    )
    if not isinstance(login, dict) or not login.get("success"):
        raise FusionSolarError("FusionSolar no aceptó la cuenta API. Revisa usuario, contraseña, vigencia y permisos.")
    token = login_headers.get("XSRF-TOKEN")
    if not token:
        token = next((cookie.value for cookie in cookies if cookie.name.upper() == "XSRF-TOKEN"), None)
    if not token:
        raise FusionSolarError("FusionSolar no entregó una sesión válida. Revisa que las Interfaces API básicas sigan activas.")
    return base_url, opener, token


def station_list(response: dict) -> list[dict]:
    raw_data = response.get("data")
    if isinstance(raw_data, dict):
        for key in ("list", "stations", "stationList"):
            if isinstance(raw_data.get(key), list):
                raw_data = raw_data[key]
                break
    if not isinstance(raw_data, list):
        return []
    plants = []
    for item in raw_data:
        if not isinstance(item, dict):
            continue
        name = item.get("stationName") or item.get("name")
        code = item.get("stationCode") or item.get("code")
        if isinstance(name, str) and name.strip():
            plants.append({"name": name.strip(), "code": str(code) if code is not None else ""})
    return plants


def fusionsolar_plants() -> list[dict]:
    """Lista plantas autorizadas con la API básica, sin llamadas de control."""
    now = time.time()
    with FUSIONSOLAR_LOCK:
        cached = FUSIONSOLAR_CACHE.get("plants")
        if cached is not None and now < float(FUSIONSOLAR_CACHE["expires"]):
            return cached
        base_url, opener, token = fusionsolar_authenticated_client()
        response, _ = fusionsolar_post(opener, f"{base_url}/thirdData/getStationList", {}, token)
        if not isinstance(response, dict) or not response.get("success"):
            raise FusionSolarError("No fue posible obtener las plantas autorizadas desde FusionSolar.")
        plants = station_list(response)
        FUSIONSOLAR_CACHE["plants"] = plants
        FUSIONSOLAR_CACHE["expires"] = now + FUSIONSOLAR_CACHE_SECONDS
        return plants


def metric_number(value):
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str) and re.fullmatch(r"[+-]?\d+(?:\.\d+)?", value.strip()):
        return float(value)
    return None


def metric_value(item: dict, *keys: str):
    expected = {re.sub(r"[^a-z0-9]", "", key.lower()) for key in keys}
    sources = (item, item.get("dataItemMap", {}))
    for source in sources:
        if isinstance(source, dict):
            for key, value in source.items():
                normalized = re.sub(r"[^a-z0-9]", "", str(key).lower())
                if normalized in expected:
                    numeric = metric_number(value)
                    if numeric is not None:
                        return numeric
    return None


def metric_keys(item: dict) -> list[str]:
    """Expone únicamente nombres de indicadores para diagnóstico; nunca valores ni secretos."""
    keys = set()
    for source in (item, item.get("dataItemMap", {})):
        if isinstance(source, dict):
            keys.update(str(key) for key in source if key not in {"stationCode", "code"})
    return sorted(keys)
    return None


def fusionsolar_overview() -> list[dict]:
    """Consulta KPI actuales de planta; no invoca control, escritura ni configuración."""
    now = time.time()
    with FUSIONSOLAR_LOCK:
        cached = FUSIONSOLAR_CACHE.get("overview")
        if cached is not None and now < float(FUSIONSOLAR_CACHE["overviewExpires"]):
            return cached
        base_url, opener, token = fusionsolar_authenticated_client()
        stations_response, _ = fusionsolar_post(opener, f"{base_url}/thirdData/getStationList", {}, token)
        if not isinstance(stations_response, dict) or not stations_response.get("success"):
            raise FusionSolarError("No fue posible obtener las plantas autorizadas desde FusionSolar.")
        plants = station_list(stations_response)
        codes = [plant["code"] for plant in plants if plant["code"]]
        if not codes:
            return plants
        kpi_response, _ = fusionsolar_post(
            opener, f"{base_url}/thirdData/getStationRealKpi", {"stationCodes": ",".join(codes)}, token
        )
        if not isinstance(kpi_response, dict) or not kpi_response.get("success"):
            raise FusionSolarError("La conexión funcionó, pero FusionSolar no entregó los indicadores en tiempo real.")
        raw_kpis = kpi_response.get("data")
        if isinstance(raw_kpis, dict):
            raw_kpis = raw_kpis.get("list") or raw_kpis.get("stations") or raw_kpis.get("data") or raw_kpis
        if isinstance(raw_kpis, dict):
            raw_kpis = [dict(value, stationCode=key) for key, value in raw_kpis.items() if isinstance(value, dict)]
        if not isinstance(raw_kpis, list):
            raw_kpis = []
        kpis_by_code = {str(item.get("stationCode") or item.get("code")): item for item in raw_kpis if isinstance(item, dict)}
        health_names = {1: "Desconectada", 2: "Con alerta", 3: "Normal"}
        overview = []
        for plant in plants:
            kpi = kpis_by_code.get(plant["code"], {})
            health = metric_value(kpi, "real_health_state", "realHealthState")
            active_power = metric_value(
                kpi,
                "active_power", "activePower", "active_power_kw", "activePowerKw",
                "inverter_power", "inverterPower", "inverter_power_kw", "inverterPowerKw",
                "pv_power", "pvPower", "pv_power_kw", "pvPowerKw", "output_power", "outputPower", "power",
            )
            overview.append({
                **plant,
                "activePower": active_power,
                "availableKpis": metric_keys(kpi) if active_power is None else [],
                "dayGeneration": metric_value(kpi, "day_power", "dayPower"),
                "monthGeneration": metric_value(kpi, "month_power", "monthPower"),
                "totalGeneration": metric_value(kpi, "total_power", "totalPower"),
                "health": health_names.get(health, "Sin estado reportado"),
            })
        FUSIONSOLAR_CACHE["plants"] = plants
        FUSIONSOLAR_CACHE["expires"] = now + FUSIONSOLAR_CACHE_SECONDS
        FUSIONSOLAR_CACHE["overview"] = overview
        FUSIONSOLAR_CACHE["overviewExpires"] = now + 60
        return overview


def report_records(response: dict) -> list[dict]:
    raw_data = response.get("data")
    if isinstance(raw_data, dict):
        raw_data = raw_data.get("list") or raw_data.get("data") or []
    return [item for item in raw_data if isinstance(item, dict)] if isinstance(raw_data, list) else []


def selected_fusionsolar_plants(plants: list[dict], selected_codes: list[str] | None) -> list[dict]:
    """Limita la consulta a plantas que la cuenta API ya tiene autorizadas."""
    if not selected_codes:
        return plants
    requested = set(selected_codes)
    available = {plant["code"] for plant in plants if plant["code"]}
    if not requested <= available:
        raise FusionSolarError("Una de las plantas seleccionadas ya no está autorizada para esta cuenta API.")
    return [plant for plant in plants if plant["code"] in requested]


def fusionsolar_daily_report(report_date: date, selected_codes: list[str] | None = None) -> dict:
    """Obtiene KPI horarios de una fecha, sin pedir datos de control de equipos."""
    today_colombia = datetime.now(timezone(timedelta(hours=-5))).date()
    if report_date > today_colombia:
        raise FusionSolarError("Selecciona una fecha de hoy o anterior.")
    cache_key = f"hour:{report_date.isoformat()}:{','.join(sorted(selected_codes or []))}"
    now = time.time()
    with FUSIONSOLAR_LOCK:
        cached = FUSIONSOLAR_CACHE["reports"].get(cache_key)
        if cached and now < cached["expires"]:
            return cached["data"]
        base_url, opener, token = fusionsolar_authenticated_client()
        stations_response, _ = fusionsolar_post(opener, f"{base_url}/thirdData/getStationList", {}, token)
        if not isinstance(stations_response, dict) or not stations_response.get("success"):
            raise FusionSolarError("No fue posible obtener las plantas autorizadas desde FusionSolar.")
        plants = selected_fusionsolar_plants(station_list(stations_response), selected_codes)
        codes = [plant["code"] for plant in plants if plant["code"]]
        if not codes:
            return {"date": cache_key, "plants": []}
        colombia_time = datetime.combine(report_date, datetime.min.time(), tzinfo=timezone(timedelta(hours=-5)))
        response, _ = fusionsolar_post(
            opener,
            f"{base_url}/thirdData/getKpiStationHour",
            {"stationCodes": ",".join(codes), "collectTime": int(colombia_time.timestamp() * 1000)},
            token,
        )
        if not isinstance(response, dict) or not response.get("success"):
            raise FusionSolarError("FusionSolar no entregó el reporte horario para esa fecha.")
        records_by_code = defaultdict(list)
        for item in report_records(response):
            code = str(item.get("stationCode") or item.get("code") or "")
            timestamp = item.get("collectTime")
            if not isinstance(timestamp, (int, float)):
                continue
            when = datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc).astimezone(timezone(timedelta(hours=-5)))
            if when.date() != report_date:
                continue
            records_by_code[code].append({
                "time": when.strftime("%H:%M"),
                "generation": metric_value(item, "inverter_power", "inverterPower", "pv_power", "pvPower"),
                "grid": metric_value(item, "ongrid_power", "ongridPower"),
                "theoretical": metric_value(item, "theory_power", "theoryPower"),
            })
        report_plants = []
        for plant in plants:
            points = sorted(records_by_code[plant["code"]], key=lambda point: point["time"])
            generation_values = [point["generation"] for point in points if point["generation"] is not None]
            grid_values = [point["grid"] for point in points if point["grid"] is not None]
            peak_point = max(
                (point for point in points if point["generation"] is not None),
                key=lambda point: point["generation"],
                default=None,
            )
            report_plants.append({
                "name": plant["name"],
                "points": points,
                "intervals": len(points),
                "generation": round(sum(generation_values), 3) if generation_values else None,
                "grid": round(sum(grid_values), 3) if grid_values else None,
                "peak": round(peak_point["generation"], 3) if peak_point else None,
                "peakTime": peak_point["time"] if peak_point else None,
            })
        result = {"date": report_date.isoformat(), "plants": report_plants}
        FUSIONSOLAR_CACHE["reports"][cache_key] = {"expires": now + FUSIONSOLAR_CACHE_SECONDS, "data": result}
        return result


def month_start(value: date) -> date:
    return value.replace(day=1)


def next_month(value: date) -> date:
    return date(value.year + (value.month == 12), 1 if value.month == 12 else value.month + 1, 1)


def fusionsolar_range_report(start_date: date, end_date: date, selected_codes: list[str] | None = None) -> dict:
    """Obtiene producción diaria para un rango corto, usando solo consultas de lectura."""
    today_colombia = datetime.now(timezone(timedelta(hours=-5))).date()
    if start_date > end_date:
        raise FusionSolarError("La fecha inicial debe ser anterior o igual a la fecha final.")
    if end_date > today_colombia:
        raise FusionSolarError("Selecciona fechas de hoy o anteriores.")
    if (end_date - start_date).days + 1 > 31:
        raise FusionSolarError("El rango máximo es de 31 días para mantener el detalle diario y respetar FusionSolar.")
    cache_key = f"range:{start_date.isoformat()}:{end_date.isoformat()}:{','.join(sorted(selected_codes or []))}"
    now = time.time()
    with FUSIONSOLAR_LOCK:
        cached = FUSIONSOLAR_CACHE["reports"].get(cache_key)
        if cached and now < cached["expires"]:
            return cached["data"]
        base_url, opener, token = fusionsolar_authenticated_client()
        stations_response, _ = fusionsolar_post(opener, f"{base_url}/thirdData/getStationList", {}, token)
        if not isinstance(stations_response, dict) or not stations_response.get("success"):
            raise FusionSolarError("No fue posible obtener las plantas autorizadas desde FusionSolar.")
        plants = selected_fusionsolar_plants(station_list(stations_response), selected_codes)
        codes = [plant["code"] for plant in plants if plant["code"]]
        if not codes:
            return {"startDate": start_date.isoformat(), "endDate": end_date.isoformat(), "plants": []}
        records_by_code: dict[str, list[dict]] = defaultdict(list)
        colombia = timezone(timedelta(hours=-5))
        cursor = month_start(start_date)
        while cursor <= end_date:
            collect_time = datetime.combine(cursor, datetime.min.time(), tzinfo=colombia)
            response, _ = fusionsolar_post(
                opener,
                f"{base_url}/thirdData/getKpiStationDay",
                {"stationCodes": ",".join(codes), "collectTime": int(collect_time.timestamp() * 1000)},
                token,
            )
            if not isinstance(response, dict) or not response.get("success"):
                raise FusionSolarError("FusionSolar no entregó producción diaria para el rango solicitado.")
            for item in report_records(response):
                code = str(item.get("stationCode") or item.get("code") or "")
                timestamp = item.get("collectTime")
                if not isinstance(timestamp, (int, float)):
                    continue
                when = datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc).astimezone(colombia).date()
                if not start_date <= when <= end_date:
                    continue
                records_by_code[code].append({
                    "date": when.isoformat(),
                    "generation": metric_value(item, "inverter_power", "inverterPower", "pv_power", "pvPower"),
                    "grid": metric_value(item, "ongrid_power", "ongridPower"),
                })
            cursor = next_month(cursor)
        report_plants = []
        for plant in plants:
            points = sorted(records_by_code[plant["code"]], key=lambda point: point["date"])
            generation_values = [point["generation"] for point in points if point["generation"] is not None]
            grid_values = [point["grid"] for point in points if point["grid"] is not None]
            peak_point = max(
                (point for point in points if point["generation"] is not None),
                key=lambda point: point["generation"],
                default=None,
            )
            report_plants.append({
                "name": plant["name"],
                "days": len(points),
                "points": points,
                "generation": round(sum(generation_values), 3) if generation_values else None,
                "grid": round(sum(grid_values), 3) if grid_values else None,
                "peak": round(peak_point["generation"], 3) if peak_point else None,
                "peakDate": peak_point["date"] if peak_point else None,
            })
        result = {"startDate": start_date.isoformat(), "endDate": end_date.isoformat(), "plants": report_plants}
        FUSIONSOLAR_CACHE["reports"][cache_key] = {"expires": now + FUSIONSOLAR_CACHE_SECONDS, "data": result}
        return result


def auth_required() -> bool:
    """En Render se protege por defecto; localmente puede iniciarse sin usuarios."""
    default = "true" if os.environ.get("PORT") else "false"
    return os.environ.get("AUTH_REQUIRED", default).lower() in {"1", "true", "yes"}


def configured_users() -> dict[str, dict[str, str]]:
    """Usuarios declarados únicamente como secreto APP_USERS_JSON en Render."""
    try:
        raw_users = json.loads(os.environ.get("APP_USERS_JSON", "{}"))
    except json.JSONDecodeError:
        return {}
    if not isinstance(raw_users, dict):
        return {}
    users = {}
    for username, details in raw_users.items():
        if not isinstance(username, str) or not isinstance(details, dict):
            continue
        password = details.get("password")
        role = details.get("role", "viewer")
        if isinstance(password, str) and role in {"admin", "technician", "viewer"}:
            users[username] = {"password": password, "role": role}
    return users


def auth_is_configured() -> bool:
    return bool(os.environ.get("APP_SESSION_SECRET")) and bool(configured_users())


def column_index(cell_reference: str) -> int:
    letters = re.match(r"[A-Z]+", cell_reference).group(0)
    result = 0
    for letter in letters:
        result = result * 26 + ord(letter) - 64
    return result - 1


def cell_value(cell: ET.Element, shared_strings: list[str] | None = None) -> str:
    if cell.get("t") == "inlineStr":
        return "".join(cell.itertext()).strip()
    value = cell.findtext(NS + "v")
    value = (value or "").strip()
    if cell.get("t") == "s" and shared_strings:
        try:
            return shared_strings[int(value)]
        except (ValueError, IndexError):
            return ""
    return value


def workbook_shared_strings(book: zipfile.ZipFile) -> list[str]:
    """Lee los encabezados cuando el XLSX usa el formato sharedStrings de Excel."""
    try:
        with book.open("xl/sharedStrings.xml") as source:
            root = ET.parse(source).getroot()
        return ["".join(item.itertext()).strip() for item in root.findall(NS + "si")]
    except KeyError:
        return []


def number(value: str) -> float | None:
    value = value.strip()
    if not value or value in {"N/A", "-"}:
        return None
    try:
        return float(value.replace(".", "").replace(",", "."))
    except ValueError:
        return None


def normalise_header(value: str) -> str:
    """Compara encabezados aunque FusionSolar cambie tildes, espacios o unidades."""
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(character for character in decomposed.lower() if character.isalnum())


def recognised_column(header: str) -> str | None:
    key = normalise_header(header)
    aliases = {
        "hour": {"horadeinicio", "horainicio", "fechahorainicio"},
        "device": {"nombredeldispositivo", "nombrededispositivo", "nombredelinversor"},
        "plant": {"nombredelsitio", "nombredelaplanta", "nombredelplanta"},
        "power": {"potenciaactivakw", "potenciaactiva"},
        "yield": {"rendimientototalkwh", "rendimientokwh", "energiatotalkwh", "energiaacumuladakwh"},
        "temperature": {"temperaturainternac", "temperaturainterna"},
        "state": {"estadodelinversor", "estadoinversor", "estadodeldispositivo"},
    }
    return next((name for name, values in aliases.items() if key in values), None)


def parse_workbook(path: Path) -> dict:
    """Lee exportes XLSX aunque varíen la hoja o la ubicación de los encabezados."""
    rows: list[dict] = []
    with zipfile.ZipFile(path) as book:
        shared_strings = workbook_shared_strings(book)
        sheets = [name for name in book.namelist() if name.startswith("xl/worksheets/") and name.endswith(".xml")]
        for sheet_name in sheets:
            with book.open(sheet_name) as sheet:
                columns: dict[int, str] = {}
                string_columns: dict[int, str] = {}
                for _, element in ET.iterparse(sheet, events=("end",)):
                    if element.tag != NS + "row":
                        continue
                    cells = {column_index(cell.get("r")): cell_value(cell, shared_strings) for cell in element.findall(NS + "c")}
                    detected = {index: recognised_column(value) for index, value in cells.items()}
                    if "hour" in detected.values() and "device" in detected.values():
                        columns = {index: kind for index, kind in detected.items() if kind}
                        string_columns = {
                            index: f"Corriente de entrada {index + 1}"
                            for index, value in cells.items()
                            if normalise_header(value).startswith("corrientedeentrada")
                        }
                    elif columns:
                        row = {
                            "Hora de inicio": cells.get(next((index for index, kind in columns.items() if kind == "hour"), -1), ""),
                            "Nombre del dispositivo": cells.get(next((index for index, kind in columns.items() if kind == "device"), -1), ""),
                        }
                        optional_fields = {
                            "plant": "Nombre del sitio", "power": "Potencia activa(kW)",
                            "yield": "Rendimiento total(kWh)", "temperature": "Temperatura interna(℃)",
                            "state": "Estado del inversor",
                        }
                        for kind, output_name in optional_fields.items():
                            source = next((index for index, column_kind in columns.items() if column_kind == kind), None)
                            if source is not None:
                                row[output_name] = cells.get(source, "")
                        for index, output_name in string_columns.items():
                            row[output_name] = cells.get(index, "")
                        if row["Hora de inicio"] and row["Nombre del dispositivo"]:
                            rows.append(row)
                    element.clear()
    return {"file": path.name, "rows": rows}


def multipart_uploads(content_type: str, body: bytes) -> list[SimpleNamespace]:
    """Extrae archivos multipart sin transformar bytes binarios de XLSX."""
    boundary_match = re.search(r'boundary=(?:"([^"]+)"|([^;\s]+))', content_type, re.IGNORECASE)
    if not boundary_match:
        return []
    boundary = (boundary_match.group(1) or boundary_match.group(2)).encode("utf-8")
    uploads = []
    for section in body.split(b"--" + boundary):
        if b"Content-Disposition: form-data" not in section or b"\r\n\r\n" not in section:
            continue
        header_bytes, content = section.split(b"\r\n\r\n", 1)
        header_text = header_bytes.decode("utf-8", errors="replace")
        field_match = re.search(r'name="([^"]+)"', header_text, re.IGNORECASE)
        filename_match = re.search(r'filename="([^"]+)"', header_text, re.IGNORECASE)
        if not field_match or field_match.group(1) != "files" or not filename_match:
            continue
        if content.endswith(b"\r\n"):
            content = content[:-2]
        uploads.append(SimpleNamespace(filename=filename_match.group(1), file=BytesIO(content)))
    return uploads


def device_name(value: str) -> str:
    match = re.search(r"(Inverter\d+)", value)
    return match.group(1) if match else value


def analyse(files: list[Path]) -> dict:
    parsed = [parse_workbook(file) for file in files]
    # La misma ventana de fechas puede volver a cargarse. Se conserva una sola
    # lectura por planta, inversor y marca de tiempo para no duplicar energía.
    records_by_key = {}
    for item in parsed:
        for row in item["rows"]:
            try:
                timestamp = datetime.strptime(row["Hora de inicio"], "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
            record = {
                "plant": row.get("Nombre del sitio", "Sin planta"),
                "device": device_name(row.get("Nombre del dispositivo", "Sin inversor")),
                "timestamp": timestamp,
                "power": number(row.get("Potencia activa(kW)", "")),
                "yield": number(row.get("Rendimiento total(kWh)", "")),
                "temperature": number(row.get("Temperatura interna(℃)", "")),
                "state": row.get("Estado del inversor", "Sin estado"),
                "stringCurrents": {name: value for name, value in row.items() if name.startswith("Corriente de entrada")},
            }
            records_by_key[(record["plant"], record["device"], record["timestamp"])] = record
    records = list(records_by_key.values())

    by_plant_device = defaultdict(list)
    by_plant = defaultdict(list)
    for record in records:
        by_plant_device[(record["plant"], record["device"])].append(record)
        by_plant[record["plant"]].append(record)

    plants = []
    daily_series = defaultdict(lambda: defaultdict(float))
    alerts = []
    diagnostics = []
    for plant, plant_records in sorted(by_plant.items()):
        devices = []
        plant_generation = 0.0
        for (key_plant, device), values in sorted(by_plant_device.items()):
            if key_plant != plant:
                continue
            values.sort(key=lambda x: x["timestamp"])
            yields = [item["yield"] for item in values if item["yield"] is not None]
            powers = [item["power"] for item in values if item["power"] is not None]
            temperatures = [item["temperature"] for item in values if item["temperature"] is not None]
            string_inputs = {field for item in values for field, reading in item["stringCurrents"].items() if reading not in {"", "N/A", "-"}}
            solar_samples = [item for item in values if (item["power"] or 0) >= 5]
            active_string_inputs = {
                field for item in solar_samples for field, reading in item["stringCurrents"].items()
                if (number(reading) or 0) > 0.2
            }
            unexpected_shutdowns = sum(
                "apagado: apagado inesperado" in item["state"].lower() and 6 <= item["timestamp"].hour <= 18
                for item in values
            )
            daily_yield = defaultdict(list)
            for item in values:
                if item["yield"] is not None:
                    daily_yield[item["timestamp"].date().isoformat()].append(item["yield"])
            generation_by_day = {day: max(day_values) - min(day_values) for day, day_values in daily_yield.items() if len(day_values) > 1}
            generation = sum(generation_by_day.values())
            plant_generation += generation
            for day, total in generation_by_day.items():
                daily_series[plant][day] += total
            devices.append({
                "name": device,
                "records": len(values),
                "generation": round(generation, 1),
                "maxPower": round(max(powers), 1) if powers else None,
                "latestPower": round(next((item["power"] for item in reversed(values) if item["power"] is not None), 0), 1),
                "latestTimestamp": values[-1]["timestamp"].strftime("%Y-%m-%d %H:%M") if values else None,
                "maxTemperature": round(max(temperatures), 1) if temperatures else None,
                "stringInputs": len(string_inputs),
                "activeStringInputs": len(active_string_inputs),
                "unexpectedShutdowns": unexpected_shutdowns,
                "states": Counter(item["state"] for item in values).most_common(3),
            })

        best_generation = max((device["generation"] for device in devices), default=0)
        for device in devices:
            issues = []
            action = "Mantener seguimiento con la próxima carga de datos."
            level = "healthy"
            if device["maxTemperature"] and device["maxTemperature"] >= 65:
                issues.append(f"Temperatura interna máxima de {device['maxTemperature']} °C")
                action = "Revisar ventilación, ventiladores, filtros y acumulación de suciedad."
                level = "critical"
            if device["unexpectedShutdowns"]:
                issues.append(f"{device['unexpectedShutdowns']} lecturas de apagado inesperado en horario diurno")
                action = "Consultar el registro de alarmas y verificar protecciones, tensión y frecuencia de red."
                level = "critical"
            if device["stringInputs"] and device["activeStringInputs"] < device["stringInputs"]:
                inactive = device["stringInputs"] - device["activeStringInputs"]
                issues.append(f"{inactive} entrada(s) FV sin corriente durante operación")
                if level != "critical":
                    level = "warning"
                    action = "Comparar corrientes y tensiones de las entradas FV; revisar fusibles, conectores, strings y sombreado."
            loss = max(best_generation - device["generation"], 0)
            if best_generation and device["generation"] < best_generation * 0.90:
                issues.append(f"Generó {loss:.0f} kWh menos que el mejor inversor comparable")
                if level == "healthy":
                    level = "warning"
                    action = "Comparar MPPT, entradas FV y estado operativo con los inversores pares."
            if not issues:
                issues.append("Sin desviaciones detectadas con las reglas actuales")
            diagnostics.append({
                "plant": plant,
                "inverter": device["name"],
                "level": level,
                "issues": issues,
                "action": action,
                "estimatedLoss": round(loss, 1),
                "stringInputs": device["stringInputs"],
                "activeStringInputs": device["activeStringInputs"],
            })

        times = sorted({item["timestamp"] for item in plant_records})
        gaps = [(later - earlier).total_seconds() / 60 for earlier, later in zip(times, times[1:])]
        long_gaps = [gap for gap in gaps if gap > 15]
        plants.append({
            "name": plant,
            "records": len(plant_records),
            "generation": round(plant_generation, 1),
            "devices": devices,
            "latestPower": round(sum(device["latestPower"] for device in devices), 1),
            "peakPower": round(sum(device["maxPower"] or 0 for device in devices), 1),
            "latestTimestamp": max((device["latestTimestamp"] for device in devices if device["latestTimestamp"]), default=None),
            "dataGaps": len(long_gaps),
            "maxGapHours": round(max(long_gaps, default=0) / 60, 1),
        })
        if long_gaps:
            alerts.append({"level": "warning", "plant": plant, "title": "Huecos de telemetría", "detail": f"{len(long_gaps)} interrupciones mayores a 15 minutos; la mayor duró {max(long_gaps) / 60:.1f} horas.", "action": "Revisar conectividad, energía del logger y la disponibilidad de la plataforma."})
        for device in devices:
            if device["maxTemperature"] and device["maxTemperature"] >= 65:
                alerts.append({"level": "danger", "plant": plant, "title": f"Temperatura alta en {device['name']}", "detail": f"Máximo registrado: {device['maxTemperature']} °C.", "action": "Inspeccionar ventilación, ventiladores y acumulación de suciedad."})

    for plant in plants:
        powers = [device["maxPower"] for device in plant["devices"] if device["maxPower"]]
        if len(powers) > 1 and min(powers) < max(powers) * 0.85:
            low = next(device for device in plant["devices"] if device["maxPower"] == min(powers))
            alerts.append({"level": "warning", "plant": plant["name"], "title": f"Pico de potencia bajo en {low['name']}", "detail": f"Máximo: {low['maxPower']} kW frente a {max(powers)} kW en su par.", "action": "Comparar strings FV, MPPT, sombreado y restricciones de operación."})

    ranking = sorted(plants, key=lambda plant: plant["generation"], reverse=True)
    comparison = None
    if len(ranking) > 1:
        highest, lowest = ranking[0], ranking[-1]
        difference = highest["generation"] - lowest["generation"]
        comparison = {"highestPlant": highest["name"], "lowestPlant": lowest["name"], "difference": round(difference, 1), "percent": round((difference / lowest["generation"] * 100) if lowest["generation"] else 0, 1)}

    return {
        "updatedAt": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "sourceFiles": [item["file"] for item in parsed],
        "summary": {"plants": len(plants), "inverters": len(by_plant_device), "records": len(records), "generation": round(sum(plant["generation"] for plant in plants), 1)},
        "plants": plants,
        "dailySeries": [{"plant": plant, "date": day, "generation": round(value, 1)} for plant, days in sorted(daily_series.items()) for day, value in sorted(days.items())],
        "alerts": alerts,
        "comparison": comparison,
        "diagnostics": diagnostics,
    }


def available_files() -> list[Path]:
    return sorted(DATA_DIR.glob("*.xlsx"), key=lambda item: item.stat().st_mtime, reverse=True)


def encode_session(username: str, role: str) -> str:
    payload = json.dumps({"user": username, "role": role, "expires": int(time.time()) + SESSION_TTL_SECONDS}, separators=(",", ":")).encode("utf-8")
    encoded = base64.urlsafe_b64encode(payload).rstrip(b"=")
    secret = os.environ["APP_SESSION_SECRET"].encode("utf-8")
    signature = hmac.new(secret, encoded, hashlib.sha256).hexdigest().encode("ascii")
    return (encoded + b"." + signature).decode("ascii")


def decode_session(token: str | None) -> dict[str, str] | None:
    if not token or not auth_is_configured() or "." not in token:
        return None
    encoded, supplied_signature = token.encode("ascii", "ignore").rsplit(b".", 1)
    secret = os.environ["APP_SESSION_SECRET"].encode("utf-8")
    expected_signature = hmac.new(secret, encoded, hashlib.sha256).hexdigest().encode("ascii")
    if not hmac.compare_digest(supplied_signature, expected_signature):
        return None
    try:
        padding = b"=" * (-len(encoded) % 4)
        payload = json.loads(base64.urlsafe_b64decode(encoded + padding))
    except (ValueError, json.JSONDecodeError):
        return None
    username, role, expires = payload.get("user"), payload.get("role"), payload.get("expires")
    if not isinstance(username, str) or role not in {"admin", "technician", "viewer"} or not isinstance(expires, int) or expires <= time.time():
        return None
    # Revoca la sesión si el usuario fue retirado o su rol cambió en Render.
    user = configured_users().get(username)
    if not user or user["role"] != role:
        return None
    return {"username": username, "role": role}


def login_allowed(client: str) -> bool:
    now = time.time()
    with LOGIN_LOCK:
        attempts = [attempt for attempt in LOGIN_ATTEMPTS[client] if now - attempt < LOGIN_WINDOW_SECONDS]
        LOGIN_ATTEMPTS[client] = attempts
        return len(attempts) < MAX_LOGIN_ATTEMPTS


def record_failed_login(client: str) -> None:
    with LOGIN_LOCK:
        LOGIN_ATTEMPTS[client].append(time.time())


class Handler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Cache-Control", "no-store")
        if os.environ.get("PORT"):
            self.send_header("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        super().end_headers()

    def client_identifier(self) -> str:
        forwarded = self.headers.get("X-Forwarded-For", "")
        return forwarded.split(",")[0].strip() or self.client_address[0]

    def current_user(self) -> dict[str, str] | None:
        if not auth_required():
            return {"username": "local", "role": "admin"}
        cookies = SimpleCookie()
        cookies.load(self.headers.get("Cookie", ""))
        session = cookies.get(SESSION_COOKIE)
        return decode_session(session.value if session else None)

    def send_login_redirect(self) -> None:
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", "/login")
        self.end_headers()

    def send_setup_required(self, api: bool) -> None:
        message = "La aplicación está protegida pero aún requiere la configuración segura de usuarios en Render."
        if api:
            self.send_json({"error": message}, HTTPStatus.SERVICE_UNAVAILABLE)
            return
        payload = f"<!doctype html><html lang='es'><meta charset='utf-8'><title>Configuración requerida</title><body><h1>Configuración de seguridad requerida</h1><p>{message}</p></body></html>".encode("utf-8")
        self.send_response(HTTPStatus.SERVICE_UNAVAILABLE)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def require_auth(self, allowed_roles: set[str] | None = None) -> dict[str, str] | None:
        is_api = urlparse(self.path).path.startswith("/api/")
        if auth_required() and not auth_is_configured():
            self.send_setup_required(is_api)
            return None
        user = self.current_user()
        if not user:
            if is_api:
                self.send_json({"error": "Inicia sesión para continuar."}, HTTPStatus.UNAUTHORIZED)
            else:
                self.send_login_redirect()
            return None
        if allowed_roles and user["role"] not in allowed_roles:
            self.send_json({"error": "Tu rol no tiene permiso para esta acción."}, HTTPStatus.FORBIDDEN)
            return None
        return user

    def session_cookie(self, token: str, max_age: int) -> str:
        secure = "; Secure" if os.environ.get("PORT") else ""
        return f"{SESSION_COOKIE}={token}; Max-Age={max_age}; Path=/; HttpOnly; SameSite=Strict{secure}"

    def handle_login(self) -> None:
        if not auth_required():
            self.send_json({"user": {"username": "local", "role": "admin"}})
            return
        if not auth_is_configured():
            self.send_setup_required(True)
            return
        client = self.client_identifier()
        if not login_allowed(client):
            self.send_json({"error": "Demasiados intentos. Intenta nuevamente en 15 minutos."}, HTTPStatus.TOO_MANY_REQUESTS)
            return
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            if not 0 < content_length <= 8192:
                raise ValueError
            credentials = json.loads(self.rfile.read(content_length))
            username = credentials.get("username", "")
            password = credentials.get("password", "")
        except (ValueError, json.JSONDecodeError):
            self.send_json({"error": "Solicitud de inicio de sesión inválida."}, HTTPStatus.BAD_REQUEST)
            return
        user = configured_users().get(username) if isinstance(username, str) else None
        if not user or not isinstance(password, str) or not hmac.compare_digest(password, user["password"]):
            record_failed_login(client)
            self.send_json({"error": "Usuario o contraseña incorrectos."}, HTTPStatus.UNAUTHORIZED)
            return
        with LOGIN_LOCK:
            LOGIN_ATTEMPTS.pop(client, None)
        token = encode_session(username, user["role"])
        self.send_json({"user": {"username": username, "role": user["role"]}}, HTTPStatus.OK, [self.session_cookie(token, SESSION_TTL_SECONDS)])

    def do_GET(self):
        route = urlparse(self.path).path
        if route in {"/login", "/static/login.css", "/static/login.js"}:
            if route == "/login":
                if auth_required() and not auth_is_configured():
                    self.send_setup_required(False)
                    return
                if self.current_user() and auth_required():
                    self.send_response(HTTPStatus.SEE_OTHER)
                    self.send_header("Location", "/")
                    self.end_headers()
                    return
                self.path = "/static/login.html"
            return super().do_GET()
        if route == "/api/me":
            user = self.require_auth()
            if user:
                self.send_json({"user": user})
            return
        if not self.require_auth():
            return
        if route == "/api/dashboard":
            # No analizar un listado mientras otra petición está sustituyendo los archivos.
            with DATA_LOCK:
                files = available_files()
                if not files:
                    self.send_json({"error": "Aún no hay archivos .xlsx cargados."}, HTTPStatus.NOT_FOUND)
                    return
                try:
                    result = analyse(files)
                    if not result["summary"]["records"]:
                        self.send_json({"error": "Los archivos cargados no contienen telemetría histórica de generación. Carga un reporte con Hora de inicio, Potencia activa y Energía/Rendimiento total."}, HTTPStatus.UNPROCESSABLE_ENTITY)
                        return
                    self.send_json(result)
                except Exception as error:
                    self.send_json({"error": f"No se pudieron analizar los archivos: {error}"}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        if route == "/api/fusionsolar/plants":
            if not self.require_auth({"admin"}):
                return
            try:
                plants = fusionsolar_plants()
                self.send_json({"provider": "FusionSolar", "plants": plants, "cachedForSeconds": FUSIONSOLAR_CACHE_SECONDS})
            except FusionSolarError as error:
                self.send_json({"error": str(error)}, HTTPStatus.BAD_GATEWAY)
            return
        if route == "/api/fusionsolar/overview":
            if not self.require_auth({"admin"}):
                return
            try:
                plants = fusionsolar_overview()
                self.send_json({"provider": "FusionSolar", "plants": plants, "cachedForSeconds": 60})
            except FusionSolarError as error:
                self.send_json({"error": str(error)}, HTTPStatus.BAD_GATEWAY)
            return
        if route == "/api/fusionsolar/daily-report":
            if not self.require_auth({"admin"}):
                return
            query = parse_qs(urlparse(self.path).query)
            selected_date = query.get("date", [""])[0]
            selected_codes = [code for code in query.get("plants", [""])[0].split(",") if code]
            try:
                report_date = date.fromisoformat(selected_date)
            except ValueError:
                self.send_json({"error": "Selecciona una fecha válida para el reporte."}, HTTPStatus.BAD_REQUEST)
                return
            try:
                self.send_json(fusionsolar_daily_report(report_date, selected_codes))
            except FusionSolarError as error:
                self.send_json({"error": str(error)}, HTTPStatus.BAD_GATEWAY)
            return
        if route == "/api/fusionsolar/range-report":
            if not self.require_auth({"admin"}):
                return
            query = parse_qs(urlparse(self.path).query)
            selected_codes = [code for code in query.get("plants", [""])[0].split(",") if code]
            try:
                start_date = date.fromisoformat(query.get("start", [""])[0])
                end_date = date.fromisoformat(query.get("end", [""])[0])
            except ValueError:
                self.send_json({"error": "Selecciona fechas válidas para el reporte de rango."}, HTTPStatus.BAD_REQUEST)
                return
            try:
                self.send_json(fusionsolar_range_report(start_date, end_date, selected_codes))
            except FusionSolarError as error:
                self.send_json({"error": str(error)}, HTTPStatus.BAD_GATEWAY)
            return
        if route == "/":
            self.path = "/static/index.html"
        return super().do_GET()

    def do_POST(self):
        route = urlparse(self.path).path
        if route == "/api/login":
            self.handle_login()
            return
        if route == "/api/logout":
            if self.require_auth():
                self.send_json({"message": "Sesión cerrada"}, HTTPStatus.OK, [self.session_cookie("", 0)])
            return
        if route != "/api/upload":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if not self.require_auth({"admin", "technician"}):
            return
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            self.send_json({"error": "Envía archivos usando multipart/form-data."}, HTTPStatus.BAD_REQUEST)
            return
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length > MAX_UPLOAD_BYTES:
            self.send_json({"error": f"El archivo excede el límite de {MAX_UPLOAD_BYTES // 1024 // 1024} MB."}, HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            return
        # cgi fue eliminado en Python 3.13. El lector conserva los bytes XLSX
        # tal como los envía el navegador, incluido Chrome para Android.
        raw_body = self.rfile.read(content_length)
        uploads = multipart_uploads(content_type, raw_body)
        valid_uploads = [(Path(upload.filename or "").name, upload) for upload in uploads]
        valid_uploads = [(filename, upload) for filename, upload in valid_uploads if filename.lower().endswith(".xlsx")]
        if not valid_uploads:
            self.send_json({"error": "Selecciona al menos un archivo XLSX válido."}, HTTPStatus.BAD_REQUEST)
            return
        # Guardar primero en un área temporal y validar registros reales. Esto
        # admite variantes de encabezados de FusionSolar sin aceptar inventarios.
        with tempfile.TemporaryDirectory() as temporary_directory:
            candidates = []
            invalid_files = []
            for filename, upload in valid_uploads:
                temporary_file = Path(temporary_directory) / f"{uuid.uuid4().hex}_{filename}"
                with temporary_file.open("wb") as output:
                    shutil.copyfileobj(upload.file, output)
                try:
                    record_count = len(parse_workbook(temporary_file)["rows"])
                except (KeyError, OSError, ET.ParseError, zipfile.BadZipFile):
                    record_count = 0
                if record_count:
                    candidates.append((filename, temporary_file))
                else:
                    invalid_files.append(filename)
            if invalid_files:
                self.send_json({"error": f"No se reemplazaron los datos. {', '.join(invalid_files)} no contiene registros históricos de telemetría. Exporta desde FusionSolar un informe por intervalos con hora, potencia y energía."}, HTTPStatus.UNPROCESSABLE_ENTITY)
                return

            with DATA_LOCK:
                # Una carga representa el período que la persona desea analizar. Limpiar
                # el lote anterior evita sumar exportaciones repetidas o de otra planta.
                for previous_file in DATA_DIR.glob("*.xlsx"):
                    previous_file.unlink()

                saved = []
                for filename, temporary_file in candidates:
                    destination = DATA_DIR / f"{uuid.uuid4().hex[:8]}_{filename}"
                    shutil.copyfile(temporary_file, destination)
                    saved.append(filename)
        self.send_json({"message": "Datos reemplazados", "files": saved}, HTTPStatus.CREATED)

    def do_DELETE(self):
        if urlparse(self.path).path != "/api/data":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if not self.require_auth({"admin"}):
            return
        with DATA_LOCK:
            deleted = 0
            for previous_file in DATA_DIR.glob("*.xlsx"):
                previous_file.unlink()
                deleted += 1
        self.send_json({"message": "Datos eliminados", "deleted": deleted})

    def send_json(self, data: dict, status: HTTPStatus = HTTPStatus.OK, cookies: list[str] | None = None):
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        for cookie in cookies or []:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format, *args):
        print(f"[{self.log_date_time_string()}] {format % args}")


if __name__ == "__main__":
    # Render entrega PORT; localmente se conserva el acceso solo desde este equipo.
    host = os.environ.get("HOST", "0.0.0.0" if "PORT" in os.environ else "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    print(f"Análisis de Plantas FV disponible en http://{host}:{port}")
    ThreadingHTTPServer((host, port), Handler).serve_forever()
