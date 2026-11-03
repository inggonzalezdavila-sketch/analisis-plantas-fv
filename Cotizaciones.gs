/** Puente privado entre MLY SolarOps y este libro de cotizaciones. */
function doPost(e) {
  try {
    var request = JSON.parse(e && e.postData ? e.postData.contents : "{}");
    var properties = PropertiesService.getScriptProperties();
    var expected = properties.getProperty("ACCESS_TOKEN") || "";
    if (!expected || expected.length < 32 || request.token !== expected) {
      throw new Error("La clave privada no es válida.");
    }
    var spreadsheetId = properties.getProperty("SPREADSHEET_ID") || "";
    if (!spreadsheetId) throw new Error("Falta configurar el ID de la hoja en las propiedades del script.");
    var spreadsheet = SpreadsheetApp.openById(spreadsheetId);
    var result;
    if (request.action === "list_sheets") {
      result = {sheets: spreadsheet.getSheets().map(function(sheet) { return sheet.getName(); })};
    } else if (request.action === "get_range") {
      result = {values: readRange_(spreadsheet, String(request.range || ""))};
    } else if (request.action === "append_quote") {
      result = appendQuote_(spreadsheet, request);
    } else {
      throw new Error("La operación solicitada no está disponible.");
    }
    return jsonOutput_({ok: true, ...result});
  } catch (error) {
    return jsonOutput_({error: String(error && error.message ? error.message : "Error de Apps Script").slice(0, 300)});
  }
}

function doGet() {
  return jsonOutput_({service: "MLY Solar Cotizaciones", ready: true});
}

/** Ejecutar una sola vez desde este proyecto vinculado a la hoja. */
function initializeConnection() {
  var spreadsheet = SpreadsheetApp.getActiveSpreadsheet();
  if (!spreadsheet) throw new Error("Abre Apps Script desde el libro de cotizaciones y vuelve a ejecutar esta función.");
  var token = [Utilities.getUuid(), Utilities.getUuid(), Utilities.getUuid()].join("-");
  PropertiesService.getScriptProperties().setProperties({
    SPREADSHEET_ID: spreadsheet.getId(),
    ACCESS_TOKEN: token
  });
  Logger.log("Copia este token privado en el secreto APPS_SCRIPT_TOKEN de Render: " + token);
  return "Conexión preparada para " + spreadsheet.getName() + ". Copia el token desde el registro de ejecución.";
}

function jsonOutput_(value) {
  return ContentService.createTextOutput(JSON.stringify(value)).setMimeType(ContentService.MimeType.JSON);
}

function readRange_(spreadsheet, reference) {
  var separator = reference.lastIndexOf("!");
  if (separator < 1) throw new Error("La referencia de hoja no es válida.");
  var name = reference.slice(0, separator).replace(/^'|'$/g, "");
  var a1 = reference.slice(separator + 1);
  var sheet = spreadsheet.getSheetByName(name);
  if (!sheet) throw new Error("No existe la pestaña " + name + ".");
  var openEnded = a1.match(/^([A-Z]+)(\d+):([A-Z]+)$/);
  var range;
  if (openEnded) {
    var startRow = Number(openEnded[2]);
    var startColumn = columnNumber_(openEnded[1]);
    var endColumn = columnNumber_(openEnded[3]);
    var rowCount = Math.max(1, sheet.getLastRow() - startRow + 1);
    range = sheet.getRange(startRow, startColumn, rowCount, endColumn - startColumn + 1);
  } else {
    if (!/^[A-Z]+\d+(?::[A-Z]+\d+)?$/.test(a1)) throw new Error("El rango solicitado no es válido.");
    range = sheet.getRange(a1);
  }
  var timezone = spreadsheet.getSpreadsheetTimeZone();
  return range.getValues().map(function(row) {
    return row.map(function(value) {
      return value instanceof Date ? Utilities.formatDate(value, timezone, "yyyy-MM-dd'T'HH:mm:ss") : value;
    });
  });
}

function columnNumber_(letters) {
  return letters.split("").reduce(function(value, letter) { return value * 26 + letter.charCodeAt(0) - 64; }, 0);
}

function appendQuote_(spreadsheet, request) {
  var registry = spreadsheet.getSheetByName("Registro_Cotizaciones");
  var details = spreadsheet.getSheetByName("Detalle_Cotizaciones");
  if (!registry || !details) throw new Error("Faltan las pestañas Registro_Cotizaciones o Detalle_Cotizaciones.");
  var registryRow = request.registry_row;
  var detailRows = request.detail_rows;
  if (!Array.isArray(registryRow) || registryRow.length !== 41 || !Array.isArray(detailRows) || !detailRows.length) {
    throw new Error("Los datos de la cotización están incompletos.");
  }
  var lock = LockService.getScriptLock();
  lock.waitLock(20000);
  var inserted = [];
  try {
    var lastRegistryRow = registry.getLastRow();
    if (lastRegistryRow >= 5) {
      var existingIds = registry.getRange(5, 1, lastRegistryRow - 4, 1).getValues();
      if (existingIds.some(function(row) { return String(row[0]) === String(registryRow[0]); })) {
        return {saved: true, duplicate: true};
      }
    }
    var nextRegistryRow = Math.max(5, lastRegistryRow + 1);
    registry.getRange(nextRegistryRow, 1, 1, 41).setValues([registryRow]);
    inserted.push({sheet: registry, row: nextRegistryRow, count: 1, columns: 41});

    var nextDetailRow = Math.max(5, details.getLastRow() + 1);
    details.getRange(nextDetailRow, 1, detailRows.length, 14).setValues(detailRows);
    inserted.push({sheet: details, row: nextDetailRow, count: detailRows.length, columns: 14});
    SpreadsheetApp.flush();
    return {saved: true, duplicate: false};
  } catch (error) {
    inserted.reverse().forEach(function(write) {
      write.sheet.getRange(write.row, 1, write.count, write.columns).clearContent();
    });
    SpreadsheetApp.flush();
    throw new Error("Google Sheets no pudo completar el registro de la cotización.");
  } finally {
    lock.releaseLock();
  }
}
