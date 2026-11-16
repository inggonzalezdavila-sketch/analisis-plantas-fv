(() => {
  const modules = {
    'inicio-view': ['.brand-header', '#home-gallery'],
    'plantas-view': ['#fusionsolar-panel', '#soliscloud-panel', '#solarman-panel'],
    'analisis-view': ['.workspace-actions', '#file-queue', '#simulation-panel', '#empty-state', '#app', '#operational-report'],
    'reportes-view': ['#fusionsolar-report-panel'],
    'cotizaciones-view': ['#quotes-panel']
  };
  const allElements = [...new Set(Object.values(modules).flatMap(selectors => selectors.flatMap(selector => [...document.querySelectorAll(selector)])))];

  function showModule(id, {scroll = false} = {}) {
    const selected = Object.hasOwn(modules, id) ? id : 'inicio-view';
    const visible = new Set(modules[selected].flatMap(selector => [...document.querySelectorAll(selector)]));
    allElements.forEach(element => element.classList.toggle('module-hidden', !visible.has(element)));
    document.querySelectorAll('[data-module-link]').forEach(link => {
      if (link.dataset.moduleLink === selected) link.setAttribute('aria-current', 'page');
      else link.removeAttribute('aria-current');
    });
    document.body.dataset.module = selected;
    if (scroll) window.scrollTo({top: 0, behavior: 'smooth'});
  }

  document.querySelectorAll('[data-module-link]').forEach(link => link.addEventListener('click', event => {
    event.preventDefault();
    if (location.hash !== `#${link.dataset.moduleLink}`) location.hash = link.dataset.moduleLink;
    else showModule(link.dataset.moduleLink, {scroll: true});
  }));
  window.addEventListener('hashchange', () => showModule(location.hash.slice(1), {scroll: true}));
  showModule(location.hash.slice(1));
})();
