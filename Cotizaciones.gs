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
    } else if (request.action === "gallery_list") {
      result = {items: listGallery_(spreadsheet, Boolean(request.include_hidden))};
    } else if (request.action === "gallery_image") {
      result = galleryImage_(spreadsheet, String(request.item_id || ""), String(request.variant || "original"), Boolean(request.include_hidden));
    } else if (request.action === "gallery_create") {
      result = createGalleryItem_(spreadsheet, request.item);
    } else if (request.action === "gallery_update") {
      result = updateGalleryItem_(spreadsheet, String(request.item_id || ""), request.item);
    } else if (request.action === "gallery_delete") {
      result = deleteGalleryItem_(spreadsheet, String(request.item_id || ""));
    } else if (request.action === "gallery_reorder") {
      result = reorderGallery_(spreadsheet, request.item_ids);
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

/** Ejecutar una sola vez para autorizar el almacenamiento de fotos en Drive. */
function initializeGalleryStorage() {
  var properties = PropertiesService.getScriptProperties();
  var spreadsheetId = properties.getProperty("SPREADSHEET_ID") || "";
  if (!spreadsheetId) throw new Error("Primero debe estar configurada la conexión actual de cotizaciones.");
  var spreadsheet = SpreadsheetApp.openById(spreadsheetId);
  var folder = galleryFolder_();
  gallerySheet_(spreadsheet);
  SpreadsheetApp.flush();
  Logger.log("Galería preparada en Drive: " + folder.getUrl());
  return "Almacenamiento de galería listo en Google Drive.";
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

var GALLERY_HEADERS_ = ["ID", "Título", "Descripción", "Ubicación", "Categoría", "Publicada", "Portada", "Orden", "Creada", "Archivo original", "Miniatura base64", "Tipo MIME"];

function gallerySheet_(spreadsheet) {
  var sheet = spreadsheet.getSheetByName("Galeria_Proyectos");
  if (!sheet) sheet = spreadsheet.insertSheet("Galeria_Proyectos");
  if (sheet.getLastRow() === 0) sheet.getRange(1, 1, 1, GALLERY_HEADERS_.length).setValues([GALLERY_HEADERS_]);
  var headers = sheet.getRange(1, 1, 1, GALLERY_HEADERS_.length).getValues()[0];
  if (headers.join("|") !== GALLERY_HEADERS_.join("|")) throw new Error("La pestaña Galeria_Proyectos tiene encabezados inesperados.");
  return sheet;
}

function galleryFolder_() {
  var properties = PropertiesService.getScriptProperties();
  var folderId = properties.getProperty("GALLERY_FOLDER_ID") || "";
  if (folderId) {
    try { return DriveApp.getFolderById(folderId); } catch (error) { properties.deleteProperty("GALLERY_FOLDER_ID"); }
  }
  var folder = DriveApp.createFolder("MLY SolarOps - Galería de proyectos");
  properties.setProperty("GALLERY_FOLDER_ID", folder.getId());
  return folder;
}

function galleryRows_(spreadsheet) {
  var sheet = gallerySheet_(spreadsheet);
  var lastRow = sheet.getLastRow();
  return lastRow < 2 ? [] : sheet.getRange(2, 1, lastRow - 1, GALLERY_HEADERS_.length).getValues().map(function(row, index) {
    return {rowNumber: index + 2, id: String(row[0] || ""), title: String(row[1] || ""), description: String(row[2] || ""),
      location: String(row[3] || ""), category: String(row[4] || "Otro"), published: row[5] === true || String(row[5]).toLowerCase() === "true",
      isCover: row[6] === true || String(row[6]).toLowerCase() === "true", sortOrder: Number(row[7] || 0), createdAt: String(row[8] || ""),
      imageFileId: String(row[9] || ""), thumbnailData: String(row[10] || ""), mimeType: String(row[11] || "image/jpeg")};
  });
}

function galleryPublicItem_(item, includeThumbnail) {
  var result = {id: item.id, title: item.title, description: item.description, location: item.location, category: item.category,
    published: item.published, isCover: item.isCover, sortOrder: item.sortOrder, createdAt: item.createdAt};
  if (includeThumbnail) result.thumbnailData = item.thumbnailData || "";
  return result;
}

function listGallery_(spreadsheet, includeHidden) {
  var items = galleryRows_(spreadsheet).filter(function(item) { return includeHidden || item.published; });
  items.sort(function(a, b) { return (Number(a.sortOrder) || 0) - (Number(b.sortOrder) || 0); });
  return items.slice(0, 100).map(function(item) { return galleryPublicItem_(item, true); });
}

function galleryImage_(spreadsheet, itemId, variant, includeHidden) {
  if (!/^[a-f0-9]{32}$/.test(itemId) || ["original", "thumbnail"].indexOf(variant) < 0) throw new Error("La fotografía solicitada no existe.");
  var item = galleryRows_(spreadsheet).find(function(entry) { return entry.id === itemId; });
  if (!item || (!includeHidden && !item.published)) throw new Error("La fotografía solicitada no existe.");
  if (variant === "thumbnail") {
    if (!item.thumbnailData) throw new Error("La miniatura no está disponible.");
    var thumbnailBytes = Utilities.base64Decode(item.thumbnailData);
    return {data: Utilities.base64Encode(thumbnailBytes), mimeType: "image/jpeg"};
  }
  var fileId = item.imageFileId;
  if (!fileId) throw new Error("La imagen no está disponible en Drive.");
  var blob = DriveApp.getFileById(fileId).getBlob();
  return {data: Utilities.base64Encode(blob.getBytes()), mimeType: blob.getContentType() || "image/jpeg"};
}

function galleryCreateFile_(folder, data, name) {
  if (typeof data !== "string" || data.length > 3200000) throw new Error("La imagen optimizada está vacía o supera el tamaño permitido.");
  var bytes = Utilities.base64Decode(data);
  if (bytes.length < 4 || bytes.length > 2400000 || (bytes[0] & 255) !== 255 || (bytes[1] & 255) !== 216 || (bytes[bytes.length - 2] & 255) !== 255 || (bytes[bytes.length - 1] & 255) !== 217) {
    throw new Error("Las imágenes deben estar optimizadas en formato JPEG y pesar menos de 2,4 MB.");
  }
  return folder.createFile(Utilities.newBlob(bytes, "image/jpeg", name));
}

function validateGalleryThumbnail_(data) {
  if (typeof data !== "string" || data.length > 45000) throw new Error("La miniatura supera el tamaño permitido.");
  var bytes = Utilities.base64Decode(data);
  if (bytes.length < 4 || bytes.length > 32000 || (bytes[0] & 255) !== 255 || (bytes[1] & 255) !== 216 || (bytes[bytes.length - 2] & 255) !== 255 || (bytes[bytes.length - 1] & 255) !== 217) {
    throw new Error("La miniatura optimizada no es un JPEG válido o supera 32 KB.");
  }
}

function validateGalleryItem_(item) {
  if (!item || typeof item !== "object" || !String(item.title || "").trim() || String(item.title).length > 160 ||
      String(item.description || "").length > 1000 || String(item.location || "").length > 180 ||
      ["Residencial", "Comercial", "Industrial", "Institucional", "Otro"].indexOf(String(item.category || "Otro")) < 0) {
    throw new Error("Revisa el título, descripción, ubicación y categoría de la fotografía.");
  }
  if (item.isCover && !item.published) throw new Error("Publica la fotografía antes de elegirla como portada.");
}

function createGalleryItem_(spreadsheet, item) {
  validateGalleryItem_(item);
  if (!/^[a-f0-9]{32}$/.test(String(item.id || ""))) throw new Error("El identificador de la fotografía no es válido.");
  var lock = LockService.getScriptLock();
  lock.waitLock(20000);
  var created = [];
  try {
    var sheet = gallerySheet_(spreadsheet), rows = galleryRows_(spreadsheet);
    if (rows.length >= 100) throw new Error("La galería admite hasta 100 fotografías. Elimina una antes de agregar otra.");
    if (rows.some(function(row) { return row.id === item.id; })) throw new Error("La fotografía ya existe.");
    validateGalleryThumbnail_(item.thumbnailData);
    var folder = galleryFolder_();
    var image = galleryCreateFile_(folder, item.imageData, item.id + ".jpg"); created.push(image);
    if (item.isCover) rows.forEach(function(row) { sheet.getRange(row.rowNumber, 7).setValue(false); });
    var sortOrder = rows.reduce(function(max, row) { return Math.max(max, Number(row.sortOrder) || 0); }, -1) + 1;
    var values = [item.id, String(item.title).trim(), String(item.description || ""), String(item.location || ""), String(item.category || "Otro"),
      Boolean(item.published), Boolean(item.isCover), sortOrder, new Date().toISOString(), image.getId(), item.thumbnailData, "image/jpeg"];
    sheet.appendRow(values); SpreadsheetApp.flush();
    return {item: galleryPublicItem_({id: item.id, title: values[1], description: values[2], location: values[3], category: values[4], published: values[5], isCover: values[6], sortOrder: sortOrder, createdAt: values[8]}, false)};
  } catch (error) {
    created.forEach(function(file) { try { file.setTrashed(true); } catch (ignored) {} });
    throw error;
  } finally { lock.releaseLock(); }
}

function updateGalleryItem_(spreadsheet, itemId, item) {
  validateGalleryItem_(item);
  if (!/^[a-f0-9]{32}$/.test(itemId)) throw new Error("La fotografía solicitada no existe.");
  var lock = LockService.getScriptLock();
  lock.waitLock(20000);
  var created = [];
  try {
    var sheet = gallerySheet_(spreadsheet), rows = galleryRows_(spreadsheet), current = rows.find(function(row) { return row.id === itemId; });
    if (!current) throw new Error("La fotografía ya no está en la galería.");
    var imageId = current.imageFileId, thumbnailData = current.thumbnailData;
    if (item.imageData || item.thumbnailData) {
      if (!item.imageData || !item.thumbnailData) throw new Error("Carga la imagen principal y la miniatura juntas.");
      validateGalleryThumbnail_(item.thumbnailData);
      var folder = galleryFolder_();
      var image = galleryCreateFile_(folder, item.imageData, itemId + "_actualizada.jpg"); created.push(image);
      imageId = image.getId(); thumbnailData = item.thumbnailData;
    }
    if (item.isCover) rows.forEach(function(row) { if (row.id !== itemId) sheet.getRange(row.rowNumber, 7).setValue(false); });
    sheet.getRange(current.rowNumber, 2, 1, 11).setValues([[
      String(item.title).trim(), String(item.description || ""), String(item.location || ""), String(item.category || "Otro"),
      Boolean(item.published), Boolean(item.isCover), current.sortOrder, current.createdAt, imageId, thumbnailData, "image/jpeg"
    ]]);
    SpreadsheetApp.flush();
    if (imageId !== current.imageFileId) { try { DriveApp.getFileById(current.imageFileId).setTrashed(true); } catch (ignored) {} }
    var saved = Object.assign({}, item, {id: itemId, sortOrder: current.sortOrder, createdAt: current.createdAt});
    delete saved.imageData; delete saved.thumbnailData;
    return {item: saved};
  } catch (error) {
    created.forEach(function(file) { try { file.setTrashed(true); } catch (ignored) {} });
    throw error;
  } finally { lock.releaseLock(); }
}

function deleteGalleryItem_(spreadsheet, itemId) {
  if (!/^[a-f0-9]{32}$/.test(itemId)) throw new Error("La fotografía solicitada no existe.");
  var lock = LockService.getScriptLock(); lock.waitLock(20000);
  try {
    var sheet = gallerySheet_(spreadsheet), item = galleryRows_(spreadsheet).find(function(row) { return row.id === itemId; });
    if (!item) throw new Error("La fotografía ya no está en la galería.");
    sheet.deleteRow(item.rowNumber);
    if (item.imageFileId) { try { DriveApp.getFileById(item.imageFileId).setTrashed(true); } catch (ignored) {} }
    SpreadsheetApp.flush(); return {deleted: true};
  } finally { lock.releaseLock(); }
}

function reorderGallery_(spreadsheet, itemIds) {
  if (!Array.isArray(itemIds) || itemIds.length > 100) throw new Error("La lista de orden de la galería no es válida.");
  var lock = LockService.getScriptLock(); lock.waitLock(20000);
  try {
    var sheet = gallerySheet_(spreadsheet), rows = galleryRows_(spreadsheet);
    if (itemIds.length !== rows.length || new Set(itemIds).size !== rows.length || rows.some(function(row) { return itemIds.indexOf(row.id) < 0; })) {
      throw new Error("La galería cambió. Recárgala y vuelve a ordenar.");
    }
    var orderById = {}; itemIds.forEach(function(id, index) { orderById[id] = index; });
    rows.forEach(function(row) { sheet.getRange(row.rowNumber, 8).setValue(orderById[row.id]); });
    SpreadsheetApp.flush(); return {saved: true};
  } finally { lock.releaseLock(); }
}
