/* Skill 中文说明镜像 — UI only; never enters Agent context. */
(() => {
  'use strict';

  const EXTENSION_ID = 'skill-zh-mirror';
  const DATA_PATH = 'extensions/skill-zh-mirror/descriptions.json';
  const originalApi = window.api;
  if (typeof originalApi !== 'function' || window.__skillZhMirrorInstalled) return;
  window.__skillZhMirrorInstalled = true;

  let cachedDescriptions = Object.create(null);
  let cacheAt = 0;
  let inflight = null;

  function dataUrl() {
    const url = new URL(DATA_PATH, document.baseURI || location.href);
    url.searchParams.set('_', String(Date.now()));
    return url.toString();
  }

  async function loadDescriptions() {
    const now = Date.now();
    if (now - cacheAt < 2000) return cachedDescriptions;
    if (inflight) return inflight;
    inflight = fetch(dataUrl(), {
      credentials: 'same-origin',
      cache: 'no-store',
      headers: { Accept: 'application/json' },
    })
      .then(response => {
        if (!response.ok) throw new Error(`Skill 中文说明数据加载失败 (${response.status})`);
        return response.json();
      })
      .then(payload => {
        const raw = payload && typeof payload.descriptions === 'object'
          ? payload.descriptions
          : Object.create(null);
        const next = Object.create(null);
        for (const [name, value] of Object.entries(raw || {})) {
          if (typeof value === 'string' && value.trim()) next[name] = value.trim();
          else if (value && typeof value.ui_description === 'string' && value.ui_description.trim()) {
            next[name] = value.ui_description.trim();
          }
        }
        cachedDescriptions = next;
        cacheAt = Date.now();
        return next;
      })
      .catch(error => {
        console.warn(`[${EXTENSION_ID}]`, error);
        return cachedDescriptions;
      })
      .finally(() => { inflight = null; });
    return inflight;
  }

  function wantsUiDescription(path) {
    try {
      const url = new URL(path, document.baseURI || location.href);
      return url.pathname.endsWith('/api/skills') || url.pathname.endsWith('/api/skills/content')
        ? url.searchParams.get('include_ui') === '1'
        : false;
    } catch (_) {
      return false;
    }
  }

  function mergeDescriptions(path, result, descriptions) {
    if (!wantsUiDescription(path) || !result || typeof result !== 'object') return result;
    if (Array.isArray(result.skills)) {
      result.skills = result.skills.map(skill => {
        if (!skill || typeof skill !== 'object') return skill;
        const localized = descriptions[skill.name];
        return localized ? { ...skill, ui_description: localized } : skill;
      });
      return result;
    }
    const url = new URL(path, document.baseURI || location.href);
    const name = url.searchParams.get('name');
    const localized = name && descriptions[name];
    if (localized) result.ui_description = localized;
    return result;
  }

  window.api = async function skillZhMirrorApi(path, opts = {}) {
    const result = await originalApi(path, opts);
    if (!wantsUiDescription(path)) return result;
    const descriptions = await loadDescriptions();
    return mergeDescriptions(path, result, descriptions);
  };

  window.SkillZhMirror = Object.freeze({
    id: EXTENSION_ID,
    refresh: async () => {
      cacheAt = 0;
      return loadDescriptions();
    },
  });

  // The host may have restored the Skills panel before this deferred extension
  // ran. Drop that English-only cache and reload it through the wrapped API.
  Promise.resolve().then(() => {
    try {
      if (typeof _skillsData !== 'undefined') _skillsData = null;
      if (typeof _currentPanel !== 'undefined' && _currentPanel === 'skills'
          && typeof loadSkills === 'function') {
        void loadSkills();
      }
    } catch (error) {
      console.warn(`[${EXTENSION_ID}] Skills 缓存刷新失败`, error);
    }
  });
})();
