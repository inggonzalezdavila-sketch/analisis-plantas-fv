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
    } else if (request.action === "update_quote") {
      result = updateQuote_(spreadsheet, request);
    } else if (request.action === "add_product") {
      result = addProduct_(spreadsheet, request.product);
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

function updateQuote_(spreadsheet, request) {
  var registry = spreadsheet.getSheetByName("Registro_Cotizaciones");
  var details = spreadsheet.getSheetByName("Detalle_Cotizaciones");
  if (!registry || !details) throw new Error("Faltan las pestañas Registro_Cotizaciones o Detalle_Cotizaciones.");
  var registryRow = request.registry_row;
  var detailRows = request.detail_rows;
  if (!Array.isArray(registryRow) || registryRow.length !== 41 || !registryRow[0] || !Array.isArray(detailRows) || !detailRows.length) {
    throw new Error("Los datos de la cotización están incompletos.");
  }
  var lock = LockService.getScriptLock();
  lock.waitLock(20000);
  var oldRegistry = null;
  var oldDetails = [];
  var appendedRows = [];
  try {
    var lastRegistryRow = registry.getLastRow();
    if (lastRegistryRow < 5) throw new Error("La cotización ya no existe en el registro.");
    var registryIds = registry.getRange(5, 1, lastRegistryRow - 4, 1).getValues();
    var registryIndex = registryIds.findIndex(function(row) { return String(row[0]) === String(registryRow[0]); });
    if (registryIndex < 0) throw new Error("La cotización ya no existe en el registro.");
    var targetRegistryRow = registryIndex + 5;
    oldRegistry = registry.getRange(targetRegistryRow, 1, 1, 41).getValues()[0];

    var lastDetailRow = details.getLastRow();
    if (lastDetailRow >= 5) {
      var detailIds = details.getRange(5, 1, lastDetailRow - 4, 1).getValues();
      detailIds.forEach(function(row, index) {
        if (String(row[0]) === String(registryRow[0])) {
          var rowNumber = index + 5;
          oldDetails.push({row: rowNumber, values: details.getRange(rowNumber, 1, 1, 14).getValues()[0]});
        }
      });
    }
    if (!oldDetails.length) throw new Error("No se encontraron los ítems de la cotización para actualizarlos.");

    registry.getRange(targetRegistryRow, 1, 1, 41).setValues([registryRow]);
    var reused = Math.min(oldDetails.length, detailRows.length);
    for (var i = 0; i < reused; i++) {
      details.getRange(oldDetails[i].row, 1, 1, 14).setValues([detailRows[i]]);
    }
    for (var j = reused; j < detailRows.length; j++) {
      var appendRow = Math.max(5, details.getLastRow() + 1);
      details.getRange(appendRow, 1, 1, 14).setValues([detailRows[j]]);
      appendedRows.push(appendRow);
    }
    for (var k = detailRows.length; k < oldDetails.length; k++) {
      details.getRange(oldDetails[k].row, 1, 1, 14).clearContent();
    }
    SpreadsheetApp.flush();
    return {saved: true, updated: true};
  } catch (error) {
    if (oldRegistry) registry.getRange(targetRegistryRow, 1, 1, 41).setValues([oldRegistry]);
    oldDetails.forEach(function(item) { details.getRange(item.row, 1, 1, 14).setValues([item.values]); });
    appendedRows.forEach(function(row) { details.getRange(row, 1, 1, 14).clearContent(); });
    SpreadsheetApp.flush();
    throw error;
  } finally {
    lock.releaseLock();
  }
}


function normalizeProductHeader_(value) {
  return String(value || "").toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "").replace(/[^a-z0-9]/g, "");
}

function addProduct_(spreadsheet, product) {
  if (!product || !product.code || !product.name) throw new Error("El equipo o servicio necesita código y descripción.");
  var sheet = spreadsheet.getSheetByName("Productos_Servicios");
  if (!sheet) throw new Error("No existe la pestaña Productos_Servicios.");
  var lock = LockService.getScriptLock();
  lock.waitLock(20000);
  try {
    var headers = sheet.getRange(5, 1, 1, 11).getValues()[0];
    var indexes = {};
    headers.forEach(function(header, index) { indexes[normalizeProductHeader_(header)] = index; });
    var codeIndex = indexes.codigo;
    var nameIndex = indexes.descripcion;
    if (codeIndex === undefined || nameIndex === undefined) throw new Error("No se encontraron las columnas Código y Descripción en Productos_Servicios.");
    var lastRow = sheet.getLastRow();
    if (lastRow >= 6) {
      var codes = sheet.getRange(6, codeIndex + 1, lastRow - 5, 1).getValues();
      if (codes.some(function(row) { return String(row[0] || "").trim().toLowerCase() === String(product.code).trim().toLowerCase(); })) {
        throw new Error("Ya existe un artículo con ese código. Usa otro código o déjalo vacío para asignar uno automáticamente.");
      }
    }
    var values = {
      codigo: String(product.code), tipo: String(product.type || "Equipo"), descripcion: String(product.name),
      marcamodelo: String(product.brand || ""), marca: String(product.brand || ""), modelo: String(product.brand || ""),
      unidad: String(product.unit || ""), precioventa: Number(product.price || 0), precio: Number(product.price || 0),
      tasaiva: Number(product.ivaRate || 0), iva: Number(product.ivaRate || 0), activo: "Sí", activa: "Sí", estado: "Activo"
    };
    var row = headers.map(function(header) { return values[normalizeProductHeader_(header)] ?? ""; });
    var nextRow = Math.max(6, lastRow + 1);
    sheet.getRange(nextRow, 1, 1, 11).setValues([row]);
    SpreadsheetApp.flush();
    return {product: product};
  } finally {
    lock.releaseLock();
  }
}
