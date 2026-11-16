(() => {
  const grid = document.querySelector('#public-gallery-grid');
  const status = document.querySelector('#public-gallery-status');
  const lightbox = document.querySelector('#public-lightbox');
  let groups = [];
  let activePhotos = [];
  let activeIndex = 0;

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[char]));
  }

  function groupPhotos(items) {
    const byProject = new Map();
    for (const item of items) {
      const match = item.title.match(/^(.*)\s+(\d+)$/);
      const baseTitle = match ? match[1].trim() : item.title;
      const key = match ? `${baseTitle.toLocaleLowerCase('es')}|${item.category}|${item.location}|${item.description}` : `single|${item.id}`;
      if (!byProject.has(key)) byProject.set(key, {baseTitle, numbered: Boolean(match), photos: []});
      byProject.get(key).photos.push(item);
    }
    return [...byProject.values()].map(group => {
      group.photos.sort((a, b) => Number(a.sortOrder || 0) - Number(b.sortOrder || 0));
      const grouped = group.numbered && group.photos.length > 1;
      return {title: grouped ? group.baseTitle : group.photos[0].title, photos: group.photos, grouped};
    });
  }

  function render() {
    if (!groups.length) {
      grid.innerHTML = '<div class="gallery-empty">Pronto compartiremos aquí nuestros proyectos.</div>';
      return;
    }
    grid.innerHTML = groups.map((group, index) => {
      const cover = group.photos.find(photo => photo.isCover) || group.photos[0];
      return `<article class="gallery-card"><button class="gallery-photo-button" type="button" data-project="${index}" aria-label="Ver ${group.photos.length} fotografías de ${escapeHtml(group.title)}"><img src="/api/public/gallery/${encodeURIComponent(cover.id)}/image?variant=thumbnail" alt="${escapeHtml(group.title)}" loading="lazy" decoding="async">${cover.isCover ? '<span class="gallery-cover-badge">Portada</span>' : ''}${group.grouped ? `<span class="gallery-photo-count">${group.photos.length} fotos</span>` : ''}</button><div class="gallery-card-body"><p class="gallery-category">${escapeHtml(cover.category)}</p><h3>${escapeHtml(group.title)}</h3>${cover.description ? `<p class="gallery-card-description">${escapeHtml(cover.description)}</p>` : ''}${cover.location ? `<small class="gallery-location">${escapeHtml(cover.location)}</small>` : ''}</div></article>`;
    }).join('');
  }

  function showPhoto(index) {
    activeIndex = (index + activePhotos.length) % activePhotos.length;
    const photo = activePhotos[activeIndex];
    document.querySelector('#public-lightbox-image').src = `/api/public/gallery/${encodeURIComponent(photo.id)}/image?variant=original`;
    document.querySelector('#public-lightbox-image').alt = photo.title;
    document.querySelector('#public-lightbox-title').textContent = activePhotos.length > 1 ? photo.title.replace(/\s+\d+$/, '') : photo.title;
    document.querySelector('#public-lightbox-category').textContent = photo.category;
    document.querySelector('#public-lightbox-description').textContent = photo.description || '';
    document.querySelector('#public-lightbox-location').textContent = photo.location || '';
    document.querySelector('#public-lightbox-counter').textContent = activePhotos.length > 1 ? `${activeIndex + 1} de ${activePhotos.length} fotos` : '';
    document.querySelector('#public-lightbox-prev').hidden = activePhotos.length < 2;
    document.querySelector('#public-lightbox-next').hidden = activePhotos.length < 2;
  }

  grid.addEventListener('click', event => {
    const button = event.target.closest('[data-project]');
    if (!button) return;
    activePhotos = groups[Number(button.dataset.project)].photos;
    showPhoto(0);
    lightbox.showModal();
  });
  document.querySelector('#public-lightbox-close').addEventListener('click', () => lightbox.close());
  document.querySelector('#public-lightbox-prev').addEventListener('click', () => showPhoto(activeIndex - 1));
  document.querySelector('#public-lightbox-next').addEventListener('click', () => showPhoto(activeIndex + 1));
  lightbox.addEventListener('click', event => { if (event.target === lightbox) lightbox.close(); });

  fetch('/api/public/gallery').then(async response => {
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'No se pudo cargar la galería.');
    const items = Array.isArray(result.items) ? result.items : [];
    groups = groupPhotos(items);
    render();
    const cover = items.find(item => item.isCover);
    if (cover) document.querySelector('.brand-header').style.setProperty('--home-cover', `url("/api/public/gallery/${encodeURIComponent(cover.id)}/image?variant=original")`);
    status.textContent = '';
  }).catch(error => {
    grid.innerHTML = '';
    status.textContent = error.message;
    status.classList.add('error');
  });
})();
