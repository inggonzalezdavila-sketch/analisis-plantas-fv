(() => {
  const grid = document.querySelector('#gallery-grid');
  const status = document.querySelector('#gallery-status');
  const adminStatus = document.querySelector('#gallery-admin-status');
  const manageToggle = document.querySelector('#gallery-manage-toggle');
  const adminPanel = document.querySelector('#gallery-admin-panel');
  const lightbox = document.querySelector('#gallery-lightbox');
  const editDialog = document.querySelector('#gallery-edit-dialog');
  const maxOriginalBytes = 2_000_000;
  let items = [];
  let isAdmin = false;
  let editingId = '';
  let lightboxPhotos = [];
  let lightboxIndex = 0;
  let renderedCards = [];

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[char]));
  }

  function setMessage(element, message, kind = '') {
    element.textContent = message;
    element.className = `gallery-status${kind ? ` ${kind}` : ''}`;
  }

  async function requestJson(url, options = {}) {
    const response = await fetch(url, options);
    let result;
    try { result = await response.json(); } catch { result = {}; }
    if (!response.ok) throw new Error(result.error || 'No fue posible completar la operación.');
    return result;
  }

  function itemImage(item) {
    return item.thumbnailData
      ? `data:image/jpeg;base64,${item.thumbnailData}`
      : `/api/gallery/${encodeURIComponent(item.id)}/image?variant=thumbnail`;
  }

  function groupForDisplay(source) {
    const groups = new Map();
    for (const item of source) {
      const match = item.title.match(/^(.*)\s+(\d+)$/);
      const baseTitle = match ? match[1].trim() : item.title;
      const key = match
        ? `${baseTitle.toLocaleLowerCase('es')}|${item.category}|${item.location}|${item.description}`
        : `single|${item.id}`;
      if (!groups.has(key)) groups.set(key, {baseTitle, numbered: Boolean(match), photos: []});
      groups.get(key).photos.push(item);
    }
    return [...groups.values()].flatMap(group => {
      const photos = group.photos.sort((a, b) => Number(a.sortOrder || 0) - Number(b.sortOrder || 0));
      const grouped = group.numbered && photos.length > 1;
      return [{
        id: photos.find(photo => photo.isCover)?.id || photos.find(photo => photo.published)?.id || photos[0].id,
        title: grouped ? group.baseTitle : photos[0].title,
        category: photos[0].category,
        description: photos[0].description,
        location: photos[0].location,
        isCover: photos.some(photo => photo.isCover),
        photos,
        grouped
      }];
    });
  }

  function render() {
    grid.classList.toggle('is-admin', isAdmin);
    grid.classList.toggle('manage-open', isAdmin && !adminPanel.hidden);
    if (!items.length) {
      grid.innerHTML = `<div class="gallery-empty">${isAdmin ? 'Aún no hay fotografías publicadas. Usa “Administrar galería” para agregar proyectos reales de MLY Solar O&amp;M.' : 'Estamos preparando la galería de proyectos de MLY Solar O&amp;M.'}</div>`;
      return;
    }
    const individualItems = isAdmin && !adminPanel.hidden;
    const cards = individualItems ? items.map(item => ({...item, photos: [item], grouped: false})) : groupForDisplay(items);
    renderedCards = cards;
    grid.innerHTML = cards.map((item, index) => {
      const coverPhoto = item.photos.find(photo => photo.isCover) || item.photos.find(photo => photo.published) || item.photos[0];
      return `<article class="gallery-card" data-id="${escapeHtml(item.id)}">
      <button class="gallery-photo-button" type="button" data-action="open" aria-label="Ampliar ${item.photos.length} fotografía(s) de ${escapeHtml(item.title)}">
        <img src="${itemImage(coverPhoto)}" alt="${escapeHtml(item.title)}" loading="lazy" decoding="async">${item.isCover ? '<span class="gallery-cover-badge">Portada</span>' : ''}${item.grouped ? `<span class="gallery-photo-count">${item.photos.length} fotos</span>` : ''}
      </button>
      <div class="gallery-card-body"><p class="gallery-category">${escapeHtml(item.category)}</p><h3>${escapeHtml(item.title)}</h3>
        ${item.description ? `<p class="gallery-card-description">${escapeHtml(item.description)}</p>` : ''}
        ${item.location ? `<small class="gallery-location">${escapeHtml(item.location)}</small>` : ''}
        ${isAdmin && individualItems ? `<div class="gallery-admin-tools" aria-label="Acciones de ${escapeHtml(item.title)}">
          <button type="button" data-action="edit">Editar</button>
          <button type="button" data-action="publish">${item.published ? 'Ocultar' : 'Publicar'}</button>
          <button type="button" data-action="cover" ${item.published ? '' : 'disabled title="Publica la fotografía primero"'}>${item.isCover ? 'Portada actual' : 'Elegir portada'}</button>
          <button type="button" data-action="up" ${index === 0 ? 'disabled' : ''} aria-label="Mover arriba">↑</button>
          <button type="button" data-action="down" ${index === cards.length - 1 ? 'disabled' : ''} aria-label="Mover abajo">↓</button>
          <button type="button" class="danger" data-action="delete">Eliminar</button>
          ${!item.published ? '<span class="gallery-hidden-label">Oculta</span>' : ''}
        </div>` : ''}</div></article>`;
    }).join('');
  }

  function applyCover() {
    const cover = items.find(item => item.isCover && item.published);
    const hero = document.querySelector('.brand-header');
    if (cover) hero.style.setProperty('--home-cover', `url("/api/gallery/${encodeURIComponent(cover.id)}/image?variant=original")`);
    else hero.style.removeProperty('--home-cover');
  }

  async function loadGallery() {
    status.className = 'gallery-status';
    status.textContent = 'Cargando proyectos…';
    try {
      const [session, gallery] = await Promise.all([requestJson('/api/me'), requestJson('/api/gallery')]);
      isAdmin = session.user?.role === 'admin';
      manageToggle.hidden = !isAdmin;
      items = Array.isArray(gallery.items) ? gallery.items : [];
      render();
      applyCover();
      status.textContent = '';
    } catch (error) {
      const activationNeeded = /operación solicitada no está disponible|operación.*no está disponible/i.test(error.message);
      if (activationNeeded) {
        const message = 'La galería está lista en la app, pero falta actualizar su conexión de Google Drive. En Apps Script, actualiza Cotizaciones.gs, ejecuta initializeGalleryStorage y publica una nueva versión.';
        setMessage(status, message, 'error');
        if (isAdmin) setMessage(adminStatus, message, 'error');
      } else {
        setMessage(status, error.message, 'error');
      }
    }
  }

  function fileAsDataUrl(blob) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onerror = () => reject(new Error('No se pudo leer una de las fotografías.'));
      reader.onload = () => resolve(String(reader.result));
      reader.readAsDataURL(blob);
    });
  }

  async function optimizedJpeg(file, maxDimension, quality, maxBytes) {
    const bitmap = await createImageBitmap(file, {imageOrientation: 'from-image'});
    try {
      let dimension = maxDimension;
      for (let attempt = 0; attempt < 4; attempt++) {
        const scale = Math.min(1, dimension / Math.max(bitmap.width, bitmap.height));
        const canvas = document.createElement('canvas');
        canvas.width = Math.max(1, Math.round(bitmap.width * scale));
        canvas.height = Math.max(1, Math.round(bitmap.height * scale));
        const context = canvas.getContext('2d', {alpha: false});
        context.fillStyle = '#fff'; context.fillRect(0, 0, canvas.width, canvas.height);
        context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
        const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/jpeg', quality));
        if (blob && blob.size <= maxBytes) return await fileAsDataUrl(blob);
        dimension = Math.round(dimension * .82);
        quality = Math.max(.58, quality - .08);
      }
      throw new Error('Una fotografía no se pudo reducir al tamaño permitido. Usa una imagen de menor resolución.');
    } finally { bitmap.close(); }
  }

  async function preparePhoto(file) {
    if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type)) throw new Error('Solo se aceptan fotografías JPEG, PNG o WebP.');
    if (file.size <= 0 || file.size > 15 * 1024 * 1024) throw new Error('Cada fotografía original debe pesar máximo 15 MB.');
    const [imageUrl, thumbnailUrl] = await Promise.all([
      optimizedJpeg(file, 1800, .82, maxOriginalBytes),
      optimizedJpeg(file, 360, .68, 32_000)
    ]);
    return {imageData: imageUrl.split(',')[1], thumbnailData: thumbnailUrl.split(',')[1]};
  }

  function safeFileTitle(file) {
    return file.name.replace(/\.[^.]+$/, '').replace(/[_-]+/g, ' ').trim().slice(0, 160) || 'Proyecto solar MLY';
  }

  manageToggle.addEventListener('click', () => { adminPanel.hidden = !adminPanel.hidden; render(); });
  document.querySelector('#gallery-admin-close').addEventListener('click', () => { adminPanel.hidden = true; render(); });
  document.querySelector('#gallery-upload-submit').addEventListener('click', async event => {
    const button = event.currentTarget;
    const input = document.querySelector('#gallery-files');
    const files = [...input.files];
    if (!files.length) { setMessage(adminStatus, 'Selecciona una o más fotografías para cargar.', 'error'); return; }
    if (files.length > 10) { setMessage(adminStatus, 'Puedes cargar hasta 10 fotografías por operación.', 'error'); return; }
    button.disabled = true;
    const baseTitle = document.querySelector('#gallery-upload-title').value.trim();
    const description = document.querySelector('#gallery-upload-description').value.trim();
    const location = document.querySelector('#gallery-upload-location').value.trim();
    const category = document.querySelector('#gallery-upload-category').value;
    const published = document.querySelector('#gallery-upload-published').checked;
    const setCover = document.querySelector('#gallery-upload-cover').checked;
    let saved = 0;
    try {
      for (let index = 0; index < files.length; index++) {
        setMessage(adminStatus, `Optimizando y guardando fotografía ${index + 1} de ${files.length}…`);
        const processed = await preparePhoto(files[index]);
        const title = baseTitle ? (files.length > 1 ? `${baseTitle} ${index + 1}` : baseTitle) : safeFileTitle(files[index]);
        await requestJson('/api/gallery/items', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({
          ...processed, title, description, location, category, published, isCover: setCover && index === 0 && published
        })});
        saved++;
      }
      input.value = '';
      document.querySelector('#gallery-upload-title').value = '';
      document.querySelector('#gallery-upload-description').value = '';
      document.querySelector('#gallery-upload-location').value = '';
      document.querySelector('#gallery-upload-cover').checked = false;
      await loadGallery();
      setMessage(adminStatus, `Se guardaron ${saved} fotografía(s) en la galería.`, 'success');
    } catch (error) {
      setMessage(adminStatus, saved ? `Se guardaron ${saved} fotografía(s). La siguiente no se pudo guardar: ${error.message}` : error.message, 'error');
    } finally { button.disabled = false; }
  });

  function showLightboxPhoto(index) {
    if (!lightboxPhotos.length) return;
    lightboxIndex = (index + lightboxPhotos.length) % lightboxPhotos.length;
    const photo = lightboxPhotos[lightboxIndex];
    document.querySelector('#gallery-lightbox-image').src = `/api/gallery/${encodeURIComponent(photo.id)}/image?variant=original`;
    document.querySelector('#gallery-lightbox-image').alt = photo.title;
    document.querySelector('#gallery-lightbox-title').textContent = lightboxPhotos.length > 1 ? photo.title.replace(/\s+\d+$/, '') : photo.title;
    document.querySelector('#gallery-lightbox-category').textContent = photo.category;
    document.querySelector('#gallery-lightbox-description').textContent = photo.description || '';
    document.querySelector('#gallery-lightbox-location').textContent = photo.location || '';
    document.querySelector('#gallery-lightbox-counter').textContent = lightboxPhotos.length > 1 ? `${lightboxIndex + 1} de ${lightboxPhotos.length} fotos` : '';
    document.querySelector('#gallery-lightbox-prev').hidden = lightboxPhotos.length < 2;
    document.querySelector('#gallery-lightbox-next').hidden = lightboxPhotos.length < 2;
  }

  function openLightbox(item) {
    lightboxPhotos = item.photos || [item];
    showLightboxPhoto(0);
    lightbox.showModal();
  }

  function openEditor(item) {
    editingId = item.id;
    document.querySelector('#gallery-edit-title').value = item.title;
    document.querySelector('#gallery-edit-description').value = item.description || '';
    document.querySelector('#gallery-edit-location').value = item.location || '';
    document.querySelector('#gallery-edit-category').value = item.category || 'Otro';
    document.querySelector('#gallery-edit-published').checked = Boolean(item.published);
    document.querySelector('#gallery-edit-cover').checked = Boolean(item.isCover);
    document.querySelector('#gallery-edit-file').value = '';
    setMessage(document.querySelector('#gallery-edit-status'), '');
    editDialog.showModal();
  }

  async function updateItem(item, changes) {
    await requestJson(`/api/gallery/items/${encodeURIComponent(item.id)}`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({
      title: item.title, description: item.description || '', location: item.location || '', category: item.category || 'Otro',
      published: Boolean(item.published), isCover: Boolean(item.isCover), ...changes
    })});
    await loadGallery();
  }

  async function moveItem(item, offset) {
    const ordered = [...items].sort((a, b) => Number(a.sortOrder || 0) - Number(b.sortOrder || 0));
    const index = ordered.findIndex(entry => entry.id === item.id);
    const target = index + offset;
    if (target < 0 || target >= ordered.length) return;
    [ordered[index], ordered[target]] = [ordered[target], ordered[index]];
    await requestJson('/api/gallery/reorder', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({itemIds: ordered.map(entry => entry.id)})});
    await loadGallery();
  }

  grid.addEventListener('click', async event => {
    const card = event.target.closest('.gallery-card');
    if (!card) return;
    const item = renderedCards.find(entry => entry.id === card.dataset.id);
    if (!item) return;
    const action = event.target.closest('[data-action]')?.dataset.action;
    if (!action || action === 'open') { openLightbox(item); return; }
    try {
      if (action === 'edit') openEditor(item);
      else if (action === 'publish') await updateItem(item, {published: !item.published, isCover: item.published ? false : item.isCover});
      else if (action === 'cover' && item.published) await updateItem(item, {isCover: true});
      else if (action === 'up') await moveItem(item, -1);
      else if (action === 'down') await moveItem(item, 1);
      else if (action === 'delete' && confirm(`¿Eliminar “${item.title}” de la galería?`)) {
        await requestJson(`/api/gallery/items/${encodeURIComponent(item.id)}`, {method: 'DELETE'});
        await loadGallery(); setMessage(adminStatus, 'Fotografía eliminada.', 'success');
      }
    } catch (error) { setMessage(adminStatus, error.message, 'error'); }
  });

  document.querySelector('#gallery-lightbox-close').addEventListener('click', () => lightbox.close());
  document.querySelector('#gallery-lightbox-prev').addEventListener('click', () => showLightboxPhoto(lightboxIndex - 1));
  document.querySelector('#gallery-lightbox-next').addEventListener('click', () => showLightboxPhoto(lightboxIndex + 1));
  lightbox.addEventListener('click', event => { if (event.target === lightbox) lightbox.close(); });
  document.querySelector('#gallery-edit-close').addEventListener('click', () => editDialog.close());
  document.querySelector('#gallery-edit-cancel').addEventListener('click', () => editDialog.close());
  editDialog.addEventListener('click', event => { if (event.target === editDialog) editDialog.close(); });
  document.querySelector('#gallery-edit-published').addEventListener('change', event => {
    if (!event.target.checked) document.querySelector('#gallery-edit-cover').checked = false;
  });
  document.querySelector('#gallery-edit-form').addEventListener('submit', async event => {
    event.preventDefault();
    const button = document.querySelector('#gallery-edit-save');
    const item = items.find(entry => entry.id === editingId);
    if (!item) { editDialog.close(); return; }
    button.disabled = true;
    try {
      setMessage(document.querySelector('#gallery-edit-status'), 'Guardando cambios…');
      const imageFile = document.querySelector('#gallery-edit-file').files[0];
      const changes = {
        title: document.querySelector('#gallery-edit-title').value.trim(),
        description: document.querySelector('#gallery-edit-description').value.trim(),
        location: document.querySelector('#gallery-edit-location').value.trim(),
        category: document.querySelector('#gallery-edit-category').value,
        published: document.querySelector('#gallery-edit-published').checked,
        isCover: document.querySelector('#gallery-edit-cover').checked
      };
      if (imageFile) Object.assign(changes, await preparePhoto(imageFile));
      await updateItem(item, changes);
      editDialog.close(); setMessage(adminStatus, 'Cambios guardados.', 'success');
    } catch (error) { setMessage(document.querySelector('#gallery-edit-status'), error.message, 'error'); }
    finally { button.disabled = false; }
  });

  loadGallery();
})();
