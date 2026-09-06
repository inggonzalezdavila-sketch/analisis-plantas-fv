"""Servidor local para Análisis de Plantas FV.

Ejecutar: python app.py
Abrir: http://127.0.0.1:8000
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
import threading
import unicodedata
import uuid
import zipfile
from collections import Counter, defaultdict
from datetime import datetime
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
STATIC_DIR = ROOT / "static"
DATA_DIR.mkdir(exist_ok=True)
NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
DATA_LOCK = threading.RLock()
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_MB", "50")) * 1024 * 1024


def column_index(cell_reference: str) -> int:
    letters = re.match(r"[A-Z]+", cell_reference).group(0)
    result = 0
    for letter in letters:
        result = result * 26 + ord(letter) - 64
    return result - 1


def cell_value(cell: ET.Element) -> str:
    if cell.get("t") == "inlineStr":
        return "".join(cell.itertext()).strip()
    value = cell.findtext(NS + "v")
    return (value or "").strip()


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
        sheets = [name for name in book.namelist() if name.startswith("xl/worksheets/") and name.endswith(".xml")]
        for sheet_name in sheets:
            with book.open(sheet_name) as sheet:
                columns: dict[int, str] = {}
                string_columns: dict[int, str] = {}
                for _, element in ET.iterparse(sheet, events=("end",)):
                    if element.tag != NS + "row":
                        continue
                    cells = {column_index(cell.get("r")): cell_value(cell) for cell in element.findall(NS + "c")}
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


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        route = urlparse(self.path).path
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
        if route == "/":
            self.path = "/static/index.html"
        return super().do_GET()

    def do_POST(self):
        if urlparse(self.path).path != "/api/upload":
            self.send_error(HTTPStatus.NOT_FOUND)
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
        with DATA_LOCK:
            deleted = 0
            for previous_file in DATA_DIR.glob("*.xlsx"):
                previous_file.unlink()
                deleted += 1
        self.send_json({"message": "Datos eliminados", "deleted": deleted})

    def send_json(self, data: dict, status: HTTPStatus = HTTPStatus.OK):
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
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
