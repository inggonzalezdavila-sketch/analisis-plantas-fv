(() => {
  const projectGrid = document.querySelector('#public-gallery-grid');
  const projectStatus = document.querySelector('#public-gallery-status');
  const servicesGrid = document.querySelector('#services-grid');
  const servicesStatus = document.querySelector('#services-status');
  const adminPage = location.pathname === '/admin/servicios';
  const adminPanel = document.querySelector('#services-admin-panel');
  const adminList = document.querySelector('#services-admin-list');
  const adminStatus = document.querySelector('#services-admin-status');
  const serviceForm = document.querySelector('#service-editor');
  const details = document.querySelector('#service-details');
  const projectLightbox = document.querySelector('#public-lightbox');
  let projectGroups = [];
  let allServices = [];
  let services = [];
  let contactUrl = '';
  let isAdmin = false;
  let editingId = '';
  let activeProjectPhotos = [];
  let activeProjectIndex = 0;
  let imageToEncode = '';

  const serviceIcons = [
    '<circle cx="12" cy="12" r="3"/><path d="M12 2v2m0 16v2M4.93 4.93l1.41 1.41m11.32 11.32 1.41 1.41M2 12h2m16 0h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41M5 15h14l-2 6H7l-2-6Zm2-3v3m5-3v3m5-3v3"/>',
    '<path d="M14.7 6.3a5 5 0 0 0-6.4 6.4L3 18l3 3 5.3-5.3a5 5 0 0 0 6.4-6.4l-3 3-3-3 3-3Z"/>',
    '<path d="m13 2-9 12h7l-1 8 10-13h-7l1-7Z"/>',
    '<path d="M6 3h9l4 4v14H6V3Zm9 0v5h4M9 12h7m-7 4h7"/>',
    '<path d="M4 19V5m0 14h17M7 16l4-5 3 2 5-7"/><circle cx="19" cy="6" r="1.5"/>',
    '<circle cx="6" cy="12" r="2.5"/><circle cx="18" cy="5" r="2.5"/><circle cx="18" cy="19" r="2.5"/><path d="m8.3 10.8 7.4-4.6m-7.4 7 7.4 4.6"/>'
  ];

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[char]));
  }

  async function requestJson(url, options = {}) {
    const response = await fetch(url, options);
    let result = {};
    try { result = await response.json(); } catch {}
    if (!response.ok) throw new Error(result.error || 'No fue posible completar la operación.');
    return result;
  }

  function groupProjects(items) {
    const groups = new Map();
    for (const item of items) {
      const title = String(item.title || '').trim().replace(/\s+/g, ' ');
      const match = title.match(/^(.*)\s+(\d+)$/);
      const baseTitle = match ? match[1].trim() : title;
      const key = [baseTitle, item.category, item.location, item.description]
        .map(value => String(value || '').trim().replace(/\s+/g, ' ').toLocaleLowerCase('es'))
        .join('|');
      if (!groups.has(key)) groups.set(key, {baseTitle, numbered: Boolean(match), photos: []});
      else if (match) groups.get(key).numbered = true;
      groups.get(key).photos.push(item);
    }
    return [...groups.values()].map(group => {
      group.photos.sort((a,b) => Number(a.sortOrder||0)-Number(b.sortOrder||0));
      const grouped = group.photos.length > 1;
      return {title:grouped?group.baseTitle:group.photos[0].title,photos:group.photos,grouped};
    });
  }

  function renderProjects() {
    if (!projectGroups.length) {
      projectGrid.innerHTML = '<div class="gallery-empty">Pronto compartiremos aquí nuestros proyectos.</div>';
      return;
    }
    projectGrid.innerHTML = projectGroups.map((group,index) => {
      const cover = group.photos.find(photo=>photo.isCover)||group.photos[0];
      return `<article class="gallery-card"><button class="gallery-photo-button" type="button" data-project="${index}" aria-label="Ver ${group.photos.length} fotografías de ${escapeHtml(group.title)}"><img src="/api/public/gallery/${encodeURIComponent(cover.id)}/image?variant=thumbnail" alt="${escapeHtml(group.title)}" loading="lazy" decoding="async">${cover.isCover?'<span class="gallery-cover-badge">Portada</span>':''}${group.grouped?`<span class="gallery-photo-count">${group.photos.length} fotos</span>`:''}</button><div class="gallery-card-body"><p class="gallery-category">${escapeHtml(cover.category)}</p><h3>${escapeHtml(group.title)}</h3>${cover.description?`<p class="gallery-card-description">${escapeHtml(cover.description)}</p>`:''}${cover.location?`<small class="gallery-location">${escapeHtml(cover.location)}</small>`:''}</div></article>`;
    }).join('');
  }

  function setProjectPhoto(index) {
    activeProjectIndex=(index+activeProjectPhotos.length)%activeProjectPhotos.length;
    const photo=activeProjectPhotos[activeProjectIndex];
    document.querySelector('#public-lightbox-image').src=`/api/public/gallery/${encodeURIComponent(photo.id)}/image?variant=original`;
    document.querySelector('#public-lightbox-image').alt=photo.title;
    document.querySelector('#public-lightbox-title').textContent=activeProjectPhotos.length>1?photo.title.replace(/\s+\d+$/,''):photo.title;
    document.querySelector('#public-lightbox-category').textContent=photo.category;
    document.querySelector('#public-lightbox-description').textContent=photo.description||'';
    document.querySelector('#public-lightbox-location').textContent=photo.location||'';
    document.querySelector('#public-lightbox-counter').textContent=activeProjectPhotos.length>1?`${activeProjectIndex+1} de ${activeProjectPhotos.length} fotos`:'';
    document.querySelector('#public-lightbox-prev').hidden=activeProjectPhotos.length<2;
    document.querySelector('#public-lightbox-next').hidden=activeProjectPhotos.length<2;
  }

  function serviceImageUrl(service, variant='thumbnail', admin=false) {
    if (variant==='thumbnail' && service.thumbnailData) return `data:image/jpeg;base64,${service.thumbnailData}`;
    return `${admin?'/api/services':'/api/public/services'}/${encodeURIComponent(service.id)}/image?variant=${variant}`;
  }

  function servicePlaceholder(index) {
    const icon=serviceIcons[index%serviceIcons.length];
    return `<div class="service-placeholder" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">${icon}</svg></div>`;
  }

  function renderServices() {
    services=allServices.filter(service=>service.published);
    if (!services.length) {
      servicesGrid.innerHTML='<div class="gallery-empty">Estamos preparando nuestro portafolio de servicios.</div>';
      return;
    }
    servicesGrid.innerHTML=services.map((service,index)=>`<article class="service-card"><div class="service-card-image">${service.imageAvailable?`<img src="${serviceImageUrl(service)}" alt="${escapeHtml(service.name)}" loading="lazy" decoding="async">`:servicePlaceholder(index)}</div><div class="service-card-content"><h3>${escapeHtml(service.name)}</h3><p>${escapeHtml(service.description)}</p><button class="clear" type="button" data-service-details="${escapeHtml(service.id)}">Ver detalles</button></div></article>`).join('');
  }

  function renderAdminServices() {
    if (!isAdmin) return;
    adminList.innerHTML=allServices.map((service,index)=>`<div class="service-admin-row" data-service-id="${escapeHtml(service.id)}"><div><b>${escapeHtml(service.name)}</b><small>${service.published?'Publicado':'Oculto'} · ${service.imageAvailable?'Fotografía cargada':'Falta fotografía'}</small></div><div class="service-admin-actions"><button type="button" data-service-action="edit">Editar</button><button type="button" data-service-action="publish">${service.published?'Ocultar':'Publicar'}</button><button type="button" data-service-action="up" ${index===0?'disabled':''} aria-label="Mover arriba">↑</button><button type="button" data-service-action="down" ${index===allServices.length-1?'disabled':''} aria-label="Mover abajo">↓</button><button type="button" class="danger" data-service-action="delete">Eliminar</button></div></div>`).join('');
  }

  function renderServiceDetails(service) {
    const photo=document.querySelector('#service-details-image');
    photo.innerHTML=service.imageAvailable?`<img src="${serviceImageUrl(service,'original')}" alt="${escapeHtml(service.name)}">`:servicePlaceholder(allServices.indexOf(service));
    document.querySelector('#service-details-name').textContent=service.name;
    document.querySelector('#service-details-description').textContent=service.description;
    document.querySelector('#service-details-list').innerHTML=(service.services||[]).map(item=>`<li>${escapeHtml(item)}</li>`).join('');
    const contactLink=document.querySelector('#service-quote-link');
    const pending=document.querySelector('#service-contact-pending');
    contactLink.hidden=false;
    if (contactUrl) {
      contactLink.href=contactUrl;
      contactLink.classList.remove('is-disabled');
      contactLink.removeAttribute('aria-disabled');
      contactLink.removeAttribute('title');
      contactLink.target=contactUrl.startsWith('https:')?'_blank':'_self';
      pending.hidden=true;
    } else {
      contactLink.href='#';
      contactLink.classList.add('is-disabled');
      contactLink.setAttribute('aria-disabled','true');
      contactLink.title='Configura PUBLIC_QUOTE_CONTACT_URL en Render';
      contactLink.target='_self';
      pending.hidden=false;
    }
    details.showModal();
  }

  async function loadServices() {
    servicesStatus.className='gallery-status';
    try {
      const data=await requestJson('/api/public/services');
      contactUrl=data.contactUrl||'';
      allServices=data.items||[];
      if (isAdmin) {
        const adminData=await requestJson('/api/services');
        allServices=adminData.items||[];
      }
      renderServices();
      renderAdminServices();
      servicesStatus.textContent='';
    } catch(error) {
      servicesStatus.textContent=error.message;
      servicesStatus.classList.add('error');
    }
  }

  function resetServiceForm() {
    editingId='';
    serviceForm.reset();
    document.querySelector('#service-image').value='';
    document.querySelector('#service-save').textContent='Crear servicio';
    document.querySelector('#service-editor-cancel').hidden=true;
    document.querySelector('#service-published').checked=true;
    adminStatus.textContent='';
    imageToEncode='';
  }

  function editService(service) {
    editingId=service.id;
    document.querySelector('#service-name').value=service.name;
    document.querySelector('#service-description').value=service.description;
    document.querySelector('#service-items').value=(service.services||[]).join('\n');
    document.querySelector('#service-published').checked=Boolean(service.published);
    document.querySelector('#service-image').value='';
    document.querySelector('#service-save').textContent='Guardar cambios';
    document.querySelector('#service-editor-cancel').hidden=false;
    adminStatus.textContent='';
    serviceForm.scrollIntoView({behavior:'smooth',block:'center'});
  }

  async function makeServiceImage(file) {
    if (!file) return {};
    if (file.size>15*1024*1024) throw new Error('La fotografía supera el máximo de 15 MB.');
    const bitmap=await createImageBitmap(file,{imageOrientation:'from-image'});
    try {
      const encode=async(maxDimension,qualities,maxBytes,label)=>{
        let dimension=maxDimension;
        for(let dimensionPass=0;dimensionPass<6;dimensionPass++,dimension=Math.max(160,Math.round(dimension*.78))){
          const scale=Math.min(1,dimension/Math.max(bitmap.width,bitmap.height));
          const canvas=document.createElement('canvas');
          canvas.width=Math.max(1,Math.round(bitmap.width*scale));canvas.height=Math.max(1,Math.round(bitmap.height*scale));
          const context=canvas.getContext('2d',{alpha:false});
          if(!context)throw new Error('El navegador no pudo preparar la fotografía.');
          context.fillStyle='#fff';context.fillRect(0,0,canvas.width,canvas.height);context.drawImage(bitmap,0,0,canvas.width,canvas.height);
          for(const quality of qualities){
            const blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/jpeg',quality));
            if(!blob)continue;
            if(blob.size<=maxBytes){
              return await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>{const dataUrl=String(reader.result||'');const comma=dataUrl.indexOf(',');if(comma<0){reject(new Error('No se pudo preparar la fotografía optimizada.'));return;}resolve(dataUrl.slice(comma+1));};reader.onerror=()=>reject(new Error('No se pudo leer la fotografía optimizada.'));reader.readAsDataURL(blob);});
            }
          }
        }
        throw new Error(`No se pudo optimizar ${label} al tamaño permitido. Prueba con otra fotografía.`);
      };
      return {
        imageData:await encode(1800,[.84,.74,.64,.54,.44,.34],2_000_000,'la imagen principal'),
        thumbnailData:await encode(560,[.76,.64,.52,.4,.3,.22],32_000,'la miniatura')
      };
    } finally {bitmap.close();}
  }

  projectGrid.addEventListener('click',event=>{
    const button=event.target.closest('[data-project]');if(!button)return;
    activeProjectPhotos=projectGroups[Number(button.dataset.project)].photos;setProjectPhoto(0);projectLightbox.showModal();
  });
  document.querySelector('#public-lightbox-close').addEventListener('click',()=>projectLightbox.close());
  document.querySelector('#public-lightbox-prev').addEventListener('click',()=>setProjectPhoto(activeProjectIndex-1));
  document.querySelector('#public-lightbox-next').addEventListener('click',()=>setProjectPhoto(activeProjectIndex+1));
  projectLightbox.addEventListener('click',event=>{if(event.target===projectLightbox)projectLightbox.close();});
  servicesGrid.addEventListener('click',event=>{const button=event.target.closest('[data-service-details]');if(!button)return;const service=services.find(item=>item.id===button.dataset.serviceDetails);if(service)renderServiceDetails(service);});
  document.querySelector('#service-details-close').addEventListener('click',()=>details.close());
  details.addEventListener('click',event=>{if(event.target===details)details.close();});
  document.querySelector('#service-quote-link').addEventListener('click',event=>{if(!contactUrl)event.preventDefault();});

  document.querySelector('#services-admin-close').addEventListener('click',()=>{adminPanel.hidden=true;});
  document.querySelector('#service-editor-cancel').addEventListener('click',resetServiceForm);
  serviceForm.addEventListener('submit',async event=>{
    event.preventDefault();
    const saveButton=document.querySelector('#service-save');saveButton.disabled=true;
    try {
      const imageFile=document.querySelector('#service-image').files[0];
      const payload={name:document.querySelector('#service-name').value.trim(),description:document.querySelector('#service-description').value.trim(),services:document.querySelector('#service-items').value.split('\n').map(value=>value.trim()).filter(Boolean),published:document.querySelector('#service-published').checked,...await makeServiceImage(imageFile)};
      await requestJson(editingId?`/api/services/${encodeURIComponent(editingId)}`:'/api/services',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
      resetServiceForm();await loadServices();adminStatus.textContent='Servicio guardado correctamente.';adminStatus.classList.add('success');
    } catch(error){adminStatus.textContent=error.message;adminStatus.classList.add('error');}
    finally{saveButton.disabled=false;}
  });

  adminList.addEventListener('click',async event=>{
    const button=event.target.closest('[data-service-action]');if(!button)return;
    const row=button.closest('[data-service-id]'),service=allServices.find(item=>item.id===row?.dataset.serviceId);if(!service)return;
    const action=button.dataset.serviceAction;
    try {
      if(action==='edit'){editService(service);return;}
      if(action==='publish'){
        await requestJson(`/api/services/${encodeURIComponent(service.id)}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:service.name,description:service.description,services:service.services,published:!service.published})});
      } else if(action==='up'||action==='down'){
        const ordered=[...allServices],index=ordered.findIndex(item=>item.id===service.id),target=index+(action==='up'?-1:1);
        if(target<0||target>=ordered.length)return;
        [ordered[index],ordered[target]]=[ordered[target],ordered[index]];
        await requestJson('/api/services/reorder',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({itemIds:ordered.map(item=>item.id)})});
      } else if(action==='delete'&&confirm(`¿Eliminar el servicio “${service.name}”?`)){
        await requestJson(`/api/services/${encodeURIComponent(service.id)}`,{method:'DELETE'});
      } else return;
      await loadServices();adminStatus.textContent='Cambios guardados.';adminStatus.classList.add('success');
    } catch(error){adminStatus.textContent=error.message;adminStatus.classList.add('error');}
  });

  async function initialize() {
    try {
      const sessionResponse=await fetch('/api/me');
      if(sessionResponse.ok){const session=await sessionResponse.json();isAdmin=session.user?.role==='admin';}
      if(adminPage&&!isAdmin){location.assign('/login');return;}
      adminPanel.hidden=!adminPage;
      if(adminPage){
        document.body.classList.add('services-admin-page');
        document.querySelector('#proyectos').hidden=true;
        document.querySelector('.brand-header').hidden=true;
        document.querySelector('#admin-return-link').hidden=false;
        document.querySelector('#public-login-link').hidden=true;
        document.querySelector('#services-title').textContent='Administrar portafolio de servicios';
      }
      try {
        if(!adminPage){
          const projectData=await requestJson('/api/public/gallery');
          const projectItems=Array.isArray(projectData.items)?projectData.items:[];
          projectGroups=groupProjects(projectItems);renderProjects();
          projectStatus.textContent='';
        }
      } catch(error) { projectStatus.textContent=error.message;projectStatus.classList.add('error'); }
      try {
        const serviceData=await requestJson('/api/public/services');
        contactUrl=serviceData.contactUrl||'';
        allServices=serviceData.items||[];
        if(adminPage){const adminData=await requestJson('/api/services');allServices=adminData.items||[];}
        renderServices();
        if(adminPage)renderAdminServices();
        servicesStatus.textContent='';
      } catch(error) { servicesStatus.textContent=error.message;servicesStatus.classList.add('error'); }
    } catch(error) {
      if(!projectGrid.children.length){projectGrid.innerHTML='';projectStatus.textContent=error.message;projectStatus.classList.add('error');}
      if(!servicesGrid.children.length){servicesStatus.textContent=error.message;servicesStatus.classList.add('error');}
    }
  }
  initialize();
})();
