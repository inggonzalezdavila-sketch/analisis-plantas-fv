(() => {
  const panel = document.querySelector('#quotes-panel');
  if (!panel) return;
  const $ = (selector, parent = document) => parent.querySelector(selector);
  const formatCOP = new Intl.NumberFormat('es-CO', { style: 'currency', currency: 'COP', maximumFractionDigits: 0 });
  const formatNumber = new Intl.NumberFormat('es-CO', { maximumFractionDigits: 2 });
  let state = { clients: [], products: [], quotes: [], company: {}, ready: false, sequence: 1 };
  const templateInput = $('#quote-template-file');
  const templateState = $('#quote-template-state');
  const newButton = $('#quote-new');
  const editor = $('#quote-editor');
  const form = $('#quote-form');
  const itemsBody = $('#quote-items');
  let editingQuoteId = null;

  const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]);
  const showMessage = (element, message, error = false) => {
    element.textContent = message;
    element.classList.toggle('error', error);
  };
  async function request(url, options = {}) {
    const response = await fetch(url, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || 'No fue posible completar la solicitud.');
    return data;
  }

  function renderHistory() {
    $('#quote-history-count').textContent = `${state.quotes.length} registradas`;
    const list = $('#quote-history-list');
    if (!state.quotes.length) {
      list.innerHTML = '<p class="quote-empty">Todavía no hay cotizaciones guardadas. Cada propuesta que guardes aparecerá aquí con su detalle.</p>';
      return;
    }
    list.innerHTML = `<div class="quote-history-scroll"><table><thead><tr><th>Número</th><th>Fecha</th><th>Cliente</th><th>Proyecto</th><th>Total</th><th>Estado</th><th></th></tr></thead><tbody>${state.quotes.map(quote => `<tr><td><b>${escapeHtml(quote.number)}</b></td><td>${escapeHtml(dateLabel(quote.date))}</td><td>${escapeHtml(quote.client)}</td><td>${escapeHtml(quote.project || '—')}</td><td>${escapeHtml(formatCOP.format(Number(quote.total)))}</td><td><span class="quote-status">${escapeHtml(quote.status)}</span></td><td><button class="clear quote-print-button" type="button" data-edit-quote="${escapeHtml(quote.id)}">Editar</button> <button class="clear quote-print-button" type="button" data-print-quote="${escapeHtml(quote.id)}">Ver / imprimir</button></td></tr>`).join('')}</tbody></table></div>`;
  }

  function dateLabel(value) {
    if (!value) return '—';
    const parts = String(value).slice(0, 10).split('-');
    return parts.length === 3 ? `${parts[2]}/${parts[1]}/${parts[0]}` : value;
  }

  function syncBootstrap(data) {
    state = data;
    const uploadLabel = $('#quote-template-label').closest('label');
    const driveStorage = data.storage === 'google_sheets';
    uploadLabel.hidden = driveStorage;
    templateInput.disabled = driveStorage;
    const updateOptions = (selector, values, fallback) => {
      const select = $(selector);
      const current = select.value;
      const choices = values?.length ? values : fallback;
      select.innerHTML = choices.map(value => `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`).join('');
      if (choices.includes(current)) select.value = current;
    };
    updateOptions('#quote-document-type', data.options?.documentTypes, ['CC', 'NIT', 'CE', 'PASAPORTE']);
    updateOptions('#quote-system-type', data.options?.systemTypes, ['ON GRID', 'OFF GRID', 'HÍBRIDO', 'MANTENIMIENTO', 'OTRO']);
    updateOptions('#quote-status', data.options?.statuses, ['Borrador', 'Enviada', 'Aprobada', 'Rechazada', 'Vencida']);
    renderHistory();
    if (data.ready) {
      if (driveStorage) {
        showMessage(templateState, `Google Drive conectado · ${data.clients.length} clientes · ${data.products.length} productos y servicios. Las cotizaciones se guardarán en la hoja de Drive.`);
      } else {
        $('#quote-template-label').textContent = 'Actualizar plantilla XLSX';
        showMessage(templateState, `Plantilla lista · ${data.clients.length} clientes · ${data.products.length} productos y servicios.`);
      }
      newButton.disabled = false;
    } else {
      showMessage(templateState, driveStorage ? 'La hoja conectada no contiene clientes y productos activos. Revisa las pestañas Clientes y Productos_Servicios en Google Drive.' : 'Carga tu archivo MLY de cotizaciones para traer clientes, catálogo y condiciones comerciales.');
      newButton.disabled = true;
    }
  }

  async function loadBootstrap() {
    try {
      const data = await request('/api/quotes');
      panel.hidden = false;
      syncBootstrap(data);
    } catch (error) {
      if (/permiso|sesión|inicia sesión/i.test(error.message)) {
        panel.hidden = true;
        const link = document.querySelector('.app-nav a[href="#quotes-panel"]');
        if (link) link.hidden = true;
        return;
      }
      panel.hidden = false;
      showMessage(templateState, error.message, true);
    }
  }

  templateInput.addEventListener('change', async () => {
    const file = templateInput.files?.[0];
    if (!file) return;
    if (!file.name.toLowerCase().endsWith('.xlsx')) {
      showMessage(templateState, 'Selecciona un archivo XLSX.', true);
      templateInput.value = '';
      return;
    }
    showMessage(templateState, 'Leyendo clientes, productos y condiciones de la plantilla…');
    const body = new FormData();
    body.append('files', file, file.name);
    try {
      const result = await request('/api/quotes/template', { method: 'POST', body });
      await loadBootstrap();
      showMessage(templateState, `Plantilla lista · ${result.clients} clientes · ${result.products} productos y servicios. La próxima será ${result.nextQuote}.`);
    } catch (error) {
      showMessage(templateState, error.message, true);
    } finally {
      templateInput.value = '';
    }
  });

  function resetForm() {
    form.reset();
    $('#quote-date').value = new Date().toLocaleDateString('en-CA');
    const defaults = state.defaults || {};
    $('#quote-validity').value = defaults.validityDays || '15';
    $('#quote-number-preview').textContent = `COT-${String(state.sequence).padStart(4, '0')}`;
    const config = state.company || {};
    $('#quote-system-type').value = [...$('#quote-system-type').options].some(option => option.value === 'HÍBRIDO') ? 'HÍBRIDO' : $('#quote-system-type').options[0]?.value || '';
    $('#quote-status').value = [...$('#quote-status').options].some(option => option.value === 'Borrador') ? 'Borrador' : $('#quote-status').options[0]?.value || '';
    $('#quote-advisor').value = defaults.advisor || '';
    $('#quote-payment').value = defaults.payment || '90% anticipo para compra de equipos e inicio de obra; 10% contra entrega y finalización.';
    $('#quote-delivery').value = defaults.delivery || 'Por definir según disponibilidad, logística y condiciones de acceso al sitio.';
    $('#quote-warranty').value = defaults.warranty || 'Según fabricante y alcance del suministro o servicio ejecutado.';
    $('#quote-transport').value = defaults.transport || 'No incluido, salvo que se indique expresamente en la propuesta.';
    $('#quote-scope').value = defaults.scope || 'Propuesta técnico-comercial para suministro, instalación, puesta en marcha o mantenimiento de sistemas solares fotovoltaicos, conforme al alcance descrito.';
    $('#quote-included').value = defaults.included || 'Suministro de equipos descritos.\nInstalación y montaje si aplica.\nConfiguración, puesta en marcha y pruebas básicas.';
    $('#quote-excluded').value = defaults.excluded || 'Obras civiles, trámites, certificaciones y trabajos no especificados en la propuesta.';
    $('#quote-notes').value = defaults.notes || '';
    itemsBody.innerHTML = '';
    addItemRow();
    updateTotals();
  }

  function setOptionValue(select, value) {
    if (!value) return;
    if (![...select.options].some(option => option.value === String(value))) select.add(new Option(String(value), String(value)));
    select.value = String(value);
  }

  function openEditor(quote = null) {
    resetForm();
    populateClientSelect();
    editingQuoteId = quote?.id || null;
    if (quote) {
      const { client = {}, project = {}, system = {}, conditions = {} } = quote;
      $('#quote-editor .eyebrow').textContent = 'EDITAR PROPUESTA';
      $('#quote-number-preview').textContent = quote.number;
      $('#quote-save').textContent = 'Guardar cambios';
      let clientIndex = state.clients.findIndex(entry => entry.id && entry.id === client.id);
      if (clientIndex < 0) clientIndex = state.clients.findIndex(entry => entry.name === client.name);
      if (clientIndex >= 0) $('#quote-client-select').value = String(clientIndex);
      $('#quote-client-name').value = client.name || '';
      setOptionValue($('#quote-document-type'), client.documentType);
      $('#quote-document').value = client.document || '';
      $('#quote-dv').value = client.dv || '';
      $('#quote-phone').value = client.phone || '';
      $('#quote-email').value = client.email || '';
      $('#quote-city').value = client.city || '';
      $('#quote-address').value = client.address || '';
      $('#quote-project').value = project.location || '';
      setOptionValue($('#quote-system-type'), project.systemType);
      $('#quote-advisor').value = project.advisor || '';
      $('#quote-date').value = String(quote.date || '').slice(0, 10);
      $('#quote-validity').value = quote.validityDays || 15;
      setOptionValue($('#quote-status'), quote.status || 'Borrador');
      $('#quote-pv-kwp').value = system.pvKwp || '';
      $('#quote-panel-w').value = system.panelW || '';
      $('#quote-panels').value = system.panels || '';
      $('#quote-inverter-kw').value = system.inverterKw || '';
      $('#quote-battery-kwh').value = system.batteryKwh || '';
      $('#quote-generation').value = system.generationKwhMonth || '';
      $('#quote-consumption').value = system.consumptionKwhMonth || '';
      $('#quote-coverage').value = system.coveragePercent || '';
      $('#quote-scope').value = conditions.scope || '';
      $('#quote-payment').value = conditions.payment || '';
      $('#quote-delivery').value = conditions.delivery || '';
      $('#quote-warranty').value = conditions.warranty || '';
      $('#quote-transport').value = conditions.transport || '';
      $('#quote-included').value = conditions.included || '';
      $('#quote-excluded').value = conditions.excluded || '';
      $('#quote-notes').value = conditions.notes || '';
      $('#quote-aiu').value = quote.totals?.aiu || 0;
      itemsBody.innerHTML = '';
      (quote.items || []).forEach(item => {
        const product = state.products.find(entry => entry.code === item.code && entry.name === item.description) || null;
        addItemRow(product);
        const row = itemsBody.lastElementChild;
        if (product) row.querySelector('.quote-product').value = String(state.products.indexOf(product));
        row.querySelector('.quote-code').value = item.code || '';
        row.querySelector('.quote-description').value = item.description || '';
        row.querySelector('.quote-brand').value = item.brand || '';
        row.querySelector('.quote-unit').value = item.unit || '';
        row.querySelector('.quote-quantity').value = item.quantity || 1;
        row.querySelector('.quote-price').value = item.unitPrice || 0;
        row.querySelector('.quote-discount').value = item.discount || 0;
        setOptionValue(row.querySelector('.quote-iva'), item.ivaRate || 0);
      });
      if (!itemsBody.rows.length) addItemRow();
      updateTotals();
    } else {
      $('#quote-editor .eyebrow').textContent = 'NUEVA PROPUESTA';
      $('#quote-save').textContent = 'Guardar cotización';
    }
    editor.hidden = false;
    editor.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  function populateClientSelect() {
    const select = $('#quote-client-select');
    select.innerHTML = '<option value="">Seleccionar o diligenciar manualmente</option>' + state.clients.map((client, index) => `<option value="${index}">${escapeHtml(client.id)} · ${escapeHtml(client.name)}</option>`).join('');
  }

  $('#quote-client-select').addEventListener('change', event => {
    if (!event.target.value) return;
    const client = state.clients[Number(event.target.value)];
    if (!client) return;
    $('#quote-client-name').value = client.name || '';
    $('#quote-document-type').value = client.documentType || 'CC';
    $('#quote-document').value = client.document || '';
    $('#quote-dv').value = client.dv || '';
    $('#quote-phone').value = client.phone || '';
    $('#quote-email').value = client.email || '';
    $('#quote-city').value = client.city || '';
    $('#quote-address').value = client.address || '';
  });

  function addItemRow(product = null) {
    const options = '<option value="">Elegir producto o diligenciar</option>' + state.products.map((entry, index) => `<option value="${index}">${escapeHtml(entry.code)} · ${escapeHtml(entry.name)}</option>`).join('');
    const rates = state.options?.ivaRates?.length ? state.options.ivaRates.map(Number) : [0, 0.05, 0.19];
    const rateOptions = [...new Set(rates)].sort((a, b) => a - b).map(rate => `<option value="${escapeHtml(rate)}" ${rate === 0.19 ? 'selected' : ''}>${escapeHtml(formatNumber.format(rate * 100))}%</option>`).join('');
    const row = document.createElement('tr');
    row.innerHTML = `<td><div class="quote-item-description"><select class="quote-product">${options}</select><input class="quote-code" maxlength="80" placeholder="Código" value="${escapeHtml(product?.code || '')}"><input class="quote-description" required maxlength="500" placeholder="Descripción del equipo o actividad" value="${escapeHtml(product?.name || '')}"><input class="quote-brand" maxlength="160" placeholder="Marca / modelo" value="${escapeHtml(product?.brand || '')}"><input class="quote-unit" maxlength="40" placeholder="Unidad" value="${escapeHtml(product?.unit || '')}"></div></td><td><input class="quote-quantity" type="number" min="0.01" step="0.01" value="1" required aria-label="Cantidad"></td><td><input class="quote-price" type="number" min="0" step="0.01" value="${escapeHtml(product?.price || '0') }" required aria-label="Precio unitario"></td><td><input class="quote-discount" type="number" min="0" step="0.01" value="0" aria-label="Descuento"></td><td><select class="quote-iva" aria-label="IVA">${rateOptions}</select></td><td class="quote-line-total">$0</td><td><button class="quote-remove clear" type="button" aria-label="Quitar ítem">×</button></td>`;
    itemsBody.append(row);
    if (product) {
      const select = $('.quote-product', row);
      const productIndex = state.products.indexOf(product);
      if (productIndex >= 0) select.value = String(productIndex);
      const iva = Number(product.ivaRate);
      if (rates.includes(iva)) $('.quote-iva', row).value = String(iva);
    }
  }

  $('#quote-add-item').addEventListener('click', () => addItemRow());
  newButton.addEventListener('click', () => openEditor());
  $('#quote-cancel').addEventListener('click', () => { editor.hidden = true; editingQuoteId = null; });
  itemsBody.addEventListener('change', event => {
    if (!event.target.matches('.quote-product')) return;
    const product = state.products[Number(event.target.value)];
    if (!product) return;
    const row = event.target.closest('tr');
    $('.quote-code', row).value = product.code || '';
    $('.quote-description', row).value = product.name || '';
    $('.quote-brand', row).value = product.brand || '';
    $('.quote-unit', row).value = product.unit || '';
    $('.quote-price', row).value = product.price || '0';
    const rate = Number(product.ivaRate);
    const availableRates = [...$('.quote-iva', row).options].map(option => Number(option.value));
    $('.quote-iva', row).value = availableRates.includes(rate) ? String(rate) : availableRates.includes(0.19) ? '0.19' : String(availableRates[availableRates.length - 1] || 0);
    updateTotals();
  });
  itemsBody.addEventListener('click', event => {
    const button = event.target.closest('.quote-remove');
    if (!button) return;
    if (itemsBody.rows.length > 1) button.closest('tr').remove();
    updateTotals();
  });
  form.addEventListener('input', updateTotals);
  form.addEventListener('change', updateTotals);

  function collectItems() {
    return [...itemsBody.rows].map(row => ({
      code: $('.quote-code', row).value.trim(), description: $('.quote-description', row).value.trim(),
      brand: $('.quote-brand', row).value.trim(), unit: $('.quote-unit', row).value.trim(),
      quantity: Number($('.quote-quantity', row).value || 0), unitPrice: Number($('.quote-price', row).value || 0),
      discount: Number($('.quote-discount', row).value || 0), ivaRate: Number($('.quote-iva', row).value || 0),
    }));
  }

  function updateTotals() {
    let subtotal = 0, discounts = 0, iva = 0;
    for (const row of itemsBody.rows) {
      const quantity = Number($('.quote-quantity', row).value || 0);
      const price = Number($('.quote-price', row).value || 0);
      const discount = Number($('.quote-discount', row).value || 0);
      const rate = Number($('.quote-iva', row).value || 0);
      const gross = quantity * price;
      const base = Math.max(0, gross - discount);
      const tax = base * rate;
      const lineTotal = base + tax;
      subtotal += gross; discounts += discount; iva += tax;
      $('.quote-line-total', row).textContent = formatCOP.format(lineTotal);
    }
    const aiu = Number($('#quote-aiu').value || 0);
    $('#quote-subtotal').textContent = formatCOP.format(subtotal);
    $('#quote-discounts').textContent = formatCOP.format(discounts);
    $('#quote-iva').textContent = formatCOP.format(iva);
    $('#quote-total').textContent = formatCOP.format(Math.max(0, subtotal - discounts + iva + aiu));
  }

  function readQuoteForm() {
    const input = id => $(`#${id}`).value.trim();
    return {
      date: input('quote-date'), validityDays: Number(input('quote-validity')), status: input('quote-status'),
      client: {
        id: state.clients.find(client => client.name === input('quote-client-name'))?.id || '',
        name: input('quote-client-name'), documentType: input('quote-document-type'), document: input('quote-document'),
        dv: input('quote-dv'), phone: input('quote-phone'), email: input('quote-email'), city: input('quote-city'), address: input('quote-address'),
      },
      project: { location: input('quote-project'), advisor: input('quote-advisor'), systemType: input('quote-system-type') },
      system: {
        pvKwp: input('quote-pv-kwp'), panelW: input('quote-panel-w'), panels: input('quote-panels'),
        inverterKw: input('quote-inverter-kw'), batteryKwh: input('quote-battery-kwh'),
        generationKwhMonth: input('quote-generation'), consumptionKwhMonth: input('quote-consumption'), coveragePercent: input('quote-coverage'),
      },
      conditions: {
        scope: input('quote-scope'), payment: input('quote-payment'), delivery: input('quote-delivery'), warranty: input('quote-warranty'),
        transport: input('quote-transport'), included: input('quote-included'), excluded: input('quote-excluded'), notes: input('quote-notes'),
      },
      aiu: Number(input('quote-aiu') || 0), items: collectItems(),
    };
  }

  form.addEventListener('submit', async event => {
    event.preventDefault();
    const button = $('#quote-save');
    button.disabled = true;
    showMessage(templateState, 'Guardando la cotización y todos sus ítems en el historial…');
    try {
      const url = editingQuoteId ? `/api/quotes/${encodeURIComponent(editingQuoteId)}/update` : '/api/quotes';
      const quote = await request(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(readQuoteForm()) });
      showMessage(templateState, `${quote.number} ${editingQuoteId ? 'actualizada' : 'guardada'} correctamente.`);
      editingQuoteId = null;
      editor.hidden = true;
      await loadBootstrap();
      printQuote(quote);
    } catch (error) {
      showMessage(templateState, error.message, true);
    } finally {
      button.disabled = false;
    }
  });

  $('#quote-history-list').addEventListener('click', async event => {
    const editButton = event.target.closest('[data-edit-quote]');
    const button = event.target.closest('[data-print-quote]');
    if (editButton) {
      try {
        const quote = await request(`/api/quotes/${encodeURIComponent(editButton.dataset.editQuote)}`);
        openEditor(quote);
      } catch (error) {
        showMessage(templateState, error.message, true);
      }
      return;
    }
    if (!button) return;
    try {
      const quote = await request(`/api/quotes/${encodeURIComponent(button.dataset.printQuote)}`);
      printQuote(quote);
    } catch (error) {
      showMessage(templateState, error.message, true);
    }
  });

  function printQuote(quote) {
    const company = state.company || {};
    const itemRows = quote.items.map(item => `<tr><td>${escapeHtml(item.line)}</td><td>${escapeHtml(item.code || '—')}</td><td>${escapeHtml(item.description)}${item.brand ? `<small>${escapeHtml(item.brand)}</small>` : ''}</td><td>${escapeHtml(item.unit || '—')}</td><td class="num">${escapeHtml(formatNumber.format(Number(item.quantity)))}</td><td class="num">${escapeHtml(formatCOP.format(Number(item.unitPrice)))}</td><td class="num">${escapeHtml(formatCOP.format(Number(item.discount)))}</td><td class="num">${escapeHtml(formatCOP.format(Number(item.total)))}</td></tr>`).join('');
    const detail = (title, value) => value ? `<section><h3>${escapeHtml(title)}</h3><p>${escapeHtml(value).replace(/\n/g, '<br>')}</p></section>` : '';
    const systemLabels = { pvKwp: 'Potencia FV (kWp)', panelW: 'Potencia panel (W)', panels: 'Número de paneles', inverterKw: 'Potencia inversor (kW)', batteryKwh: 'Capacidad batería (kWh)', generationKwhMonth: 'Generación estimada (kWh/mes)', consumptionKwhMonth: 'Consumo estimado (kWh/mes)', coveragePercent: 'Cobertura estimada (%)' };
    const systemPairs = Object.entries(quote.system || {}).filter(([, value]) => value !== '').map(([key, value]) => `<div><span>${escapeHtml(systemLabels[key] || key)}</span><b>${escapeHtml(value)}</b></div>`).join('');
    const html = `<!doctype html><html lang="es"><meta charset="utf-8"><title>${escapeHtml(quote.number)} · ${escapeHtml(quote.client.name)}</title><style>
      *{box-sizing:border-box}body{font:13px Arial,sans-serif;color:#15243b;margin:30px auto;max-width:1050px;padding:0 24px}header{display:flex;justify-content:space-between;gap:24px;border-bottom:3px solid #efb51c;padding-bottom:16px}h1{margin:0;color:#102b4e;font-size:24px}h2{color:#102b4e;font-size:17px;margin:5px 0}h3{font-size:12px;text-transform:uppercase;letter-spacing:.7px;color:#567065;margin:0 0 6px}p{margin:0;line-height:1.5}.number{text-align:right;white-space:nowrap}.number b{font-size:17px;color:#d38a00}.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:18px 0}.card{background:#f4f8f5;border:1px solid #dce7df;border-radius:8px;padding:11px}.card span,.system span{display:block;color:#64746c;font-size:10px;margin-bottom:4px}.system{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin:16px 0}.system div{padding:9px;border:1px solid #e0e7e2;border-radius:6px}table{border-collapse:collapse;width:100%;margin:16px 0;font-size:11px}th{background:#102b4e;color:white;text-align:left}th,td{padding:8px;border-bottom:1px solid #dfe7e2;vertical-align:top}td small{display:block;color:#65756d;margin-top:4px}.num{text-align:right;white-space:nowrap}.totals{margin-left:auto;width:min(360px,100%)}.totals div{display:flex;justify-content:space-between;border-bottom:1px solid #e2e9e4;padding:6px}.totals .final{font-size:17px;font-weight:bold;color:#102b4e;border-top:2px solid #efb51c}.sections{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:22px}.sections section{border-top:1px solid #dce7df;padding-top:10px}footer{border-top:1px solid #dce7df;margin-top:22px;padding-top:12px;color:#65756d;display:flex;justify-content:space-between}.print{position:fixed;right:24px;top:20px;background:#147e5c;color:white;border:0;border-radius:7px;padding:10px 15px;font-weight:bold;cursor:pointer}@media print{body{margin:0 auto;padding:0;max-width:none}.print{display:none}header,.card,th{-webkit-print-color-adjust:exact;print-color-adjust:exact}@page{size:letter;margin:13mm}}@media(max-width:650px){body{padding:0 12px}.grid{grid-template-columns:1fr 1fr}.system{grid-template-columns:1fr 1fr}.sections{grid-template-columns:1fr}table{font-size:9px}th,td{padding:5px}}
    </style><body><button class="print" onclick="window.print()">Imprimir / guardar PDF</button><header><div><h1>${escapeHtml(company['Razón social'] || 'MLY SOLAR O&M S.A.S.')}</h1><p>Soluciones solares, diseño, suministro e instalación</p><p>${escapeHtml(company['NIT'] || '')} · ${escapeHtml(company['Teléfono'] || '')} · ${escapeHtml(company['Correo'] || '')}</p></div><div class="number"><h2>PROPUESTA TÉCNICO-COMERCIAL</h2><p><b>${escapeHtml(quote.number)}</b></p><p>Fecha: ${escapeHtml(dateLabel(quote.date))} · Vigencia: ${escapeHtml(quote.validityDays)} días</p><p>Válida hasta: ${escapeHtml(dateLabel(quote.validUntil))}</p></div></header>
      <div class="grid"><div class="card"><h3>Cliente</h3><p><b>${escapeHtml(quote.client.name)}</b></p><p>${escapeHtml(quote.client.documentType)} ${escapeHtml(quote.client.document)}${quote.client.dv ? `-${escapeHtml(quote.client.dv)}` : ''}</p><p>${escapeHtml(quote.client.phone)} · ${escapeHtml(quote.client.email)}</p></div><div class="card"><h3>Proyecto / ubicación</h3><p><b>${escapeHtml(quote.project.location || '—')}</b></p><p>${escapeHtml(quote.client.address || '')}</p><p>${escapeHtml(quote.client.city || '')}</p></div><div class="card"><h3>Propuesta</h3><p><b>${escapeHtml(quote.project.systemType || '—')}</b></p><p>Asesor: ${escapeHtml(quote.project.advisor || '—')}</p><p>Estado: ${escapeHtml(quote.status)}</p></div></div>
      ${systemPairs ? `<h3>Resumen técnico</h3><div class="system">${systemPairs}</div>` : ''}<h3>Equipos y actividades</h3><table><thead><tr><th>Ítem</th><th>Código</th><th>Descripción</th><th>Und.</th><th>Cant.</th><th>Valor unit.</th><th>Descuento</th><th>Total línea</th></tr></thead><tbody>${itemRows}</tbody></table><div class="totals"><div><span>Subtotal antes de descuentos</span><b>${escapeHtml(formatCOP.format(Number(quote.totals.subtotal)))}</b></div><div><span>Descuentos</span><b>${escapeHtml(formatCOP.format(Number(quote.totals.discounts)))}</b></div><div><span>IVA</span><b>${escapeHtml(formatCOP.format(Number(quote.totals.iva)))}</b></div><div><span>AIU</span><b>${escapeHtml(formatCOP.format(Number(quote.totals.aiu)))}</b></div><div class="final"><span>TOTAL COTIZADO</span><b>${escapeHtml(formatCOP.format(Number(quote.totals.total)))}</b></div></div>
      <div class="sections">${detail('Alcance de la propuesta', quote.conditions.scope)}${detail('Forma de pago', quote.conditions.payment)}${detail('Tiempo de entrega / ejecución', quote.conditions.delivery)}${detail('Garantía', quote.conditions.warranty)}${detail('Transporte', quote.conditions.transport)}${detail('Alcance incluido', quote.conditions.included)}${detail('No incluye', quote.conditions.excluded)}${detail('Observaciones', quote.conditions.notes)}</div><footer><span>${escapeHtml(company['Ciudad / sede'] || 'Riohacha, La Guajira, Colombia')}</span><b>ENERGÍA LIMPIA · AHORRO · SOSTENIBILIDAD</b></footer></body></html>`;
    const popup = window.open('', '_blank');
    if (!popup) {
      showMessage(templateState, 'El navegador bloqueó la ventana de la propuesta. Permite las ventanas emergentes para verla e imprimirla.', true);
      return;
    }
    popup.document.open();
    popup.document.write(html);
    popup.document.close();
  }

  void loadBootstrap();
})();
