/* PCS public presentation only. No privileged actions or external resources. */
(function (root) {
  'use strict';
  const object = value => value && typeof value === 'object' && !Array.isArray(value) ? value : {};
  const text = (value, fallback = 'Unavailable') =>
    (typeof value === 'string' && value.trim()) || (typeof value === 'number' && Number.isFinite(value)) ? String(value).slice(0, 300) : fallback;
  const states = {ok: '● OK', warn: '▲ WARN', bad: '✕ BAD'};
  function optional(section) {
    if (section.configured === false) return 'Not configured';
    return section.configured === true && Object.hasOwn(states, section.status) ? states[section.status] : 'Unknown';
  }
  function watts(value) {
    if (typeof value === 'string' && /^-?\d+(\.\d+)?\s*W$/i.test(value.trim())) value = Number.parseFloat(value);
    return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(1) + ' W' : 'Unavailable';
  }
  function safeURL(value) {
    if (typeof value !== 'string') return null;
    try {
      const url = new URL(value);
      return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password ? url.href : null;
    } catch (_) { return null; }
  }
  function viewModel(raw) {
    const data = object(raw), network = object(data.network), gps = object(data.gnss);
    const system = object(data.system), power = object(data.power), aprs = object(data.aprs);
    const mesh = object(data.meshtastic), time = object(data.time), pistar = object(data.pistar);
    const state = Object.hasOwn(states, data.overall) ? data.overall : null;
    const offline = data.offline === true || network.offline === true;
    const inputWatts = power.configured === true && power.input_online === true ? watts(power.input_power) : 'Unavailable';
    const clients = typeof network.ap_client_count === 'number' && Number.isInteger(network.ap_client_count) && network.ap_client_count >= 0 ? String(network.ap_client_count) : 'Unavailable';
    return {
      state, health: state ? states[state] + (offline ? ' - OFFLINE' : '') : '— Status unavailable',
      callsign: text(aprs.callsign, 'PCS FIELD STATION'), localTime: text(system.local_time), uptime: text(system.uptime),
      uplink: offline || network.internet_available === false ? 'Offline' : text(network.uplink_type),
      coordinates: text(gps.coordinates), grid: text(gps.grid_square),
      voltage: power.configured === true && power.input_online === true && typeof power.input_voltage === "number" && Number.isFinite(power.input_voltage) ? power.input_voltage.toFixed(2) + " V" : "Unavailable",
      gps: text(gps.fix), power: inputWatts, clients, aprs: optional(aprs), mesh: optional(mesh),
      timeSource: text(time.source, 'Unknown'),
      pistar: pistar.configured === false ? 'Not configured' : pistar.configured === true ? (pistar.online === true ? 'Online' : pistar.online === false ? 'Offline' : 'Unknown') : 'Unknown',
      pistarURL: pistar.configured === true ? safeURL(pistar.url) : null,
      generated: text(data.generated_at, 'time unavailable'),
      alerts: Array.isArray(data.alerts) ? data.alerts.filter(a => a && ['warn', 'bad'].includes(a.severity)).slice(0, 32) : [],
      error: Boolean(data.error), data
    };
  }
  const api = {viewModel, safeURL};
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (!root.document) return;
  root.PCS = api;
  const doc = root.document;
  let hasSample = false, running = false, timer;
  function setFields(model) {
    doc.querySelectorAll('[data-field]').forEach(el => { el.textContent = text(model[el.dataset.field], 'Unknown'); });
    doc.querySelectorAll('[data-value]').forEach(el => {
      const [section, field] = el.dataset.value.split('.');
      const values = object(model.data[section]);
      el.textContent = values.configured === false ? 'Not configured' : text(values[field]);
    });
    const badge = doc.getElementById('health');
    if (badge) { badge.textContent = model.health; badge.dataset.state = model.state || ''; }
    const caption = doc.getElementById('health-caption');
    if (caption) caption.textContent = model.error ? 'Collector reported a fault' : model.state ? 'Current PCS health' : 'No health reading';
    const list = doc.getElementById('alerts');
    if (list) {
      list.replaceChildren();
      model.alerts.forEach(alert => {
        const item = doc.createElement('li');
        item.textContent = `${states[alert.severity]} · ${text(alert.component, 'PCS')}: ${text(alert.message, 'Needs attention')}`;
        list.appendChild(item);
      });
      list.hidden = !model.alerts.length;
    }
    const tile = doc.getElementById('pistar-tile');
    if (tile) tile.setAttribute('href', model.pistarURL || '/pistar/');
    const link = doc.getElementById('pistar-open');
    if (link) { link.hidden = !model.pistarURL; if (model.pistarURL) link.href = model.pistarURL; else link.removeAttribute('href'); }
  }
  function unavailable() {
    const note = doc.getElementById('status-note');
    if (note) { note.textContent = hasSample ? 'PCS status unavailable. Readings below are last known; service links remain available.' : 'PCS status unavailable. Service links remain available.'; note.dataset.stale = 'true'; }
    const badge = doc.getElementById('health');
    if (badge) { badge.textContent = '— Status unavailable'; badge.dataset.state = ''; }
    const caption = doc.getElementById('health-caption');
    if (caption) caption.textContent = hasSample ? 'Last known readings' : 'No current readings';
  }
  async function refresh() {
    if (running) return;
    running = true;
    clearTimeout(timer);
    const controller = new AbortController();
    const deadline = setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch('/api/public-status', {cache: 'no-store', credentials: 'omit', signal: controller.signal});
      if (!response.ok) throw new Error('Status request failed');
      const data = await response.json();
      if (!data || typeof data !== 'object' || Array.isArray(data)) throw new Error('Invalid status');
      const model = viewModel(data);
      setFields(model); hasSample = true;
      const note = doc.getElementById('status-note');
      if (note) { note.textContent = model.error ? 'PCS status collection reported a fault. See Status for details.' : 'Status updated · PCS sample ' + model.generated; note.dataset.stale = 'false'; }
    } catch (_) { unavailable(); }
    finally { clearTimeout(deadline); running = false; timer = setTimeout(refresh, 8000); }
  }
  doc.addEventListener('visibilitychange', () => { if (!doc.hidden) refresh(); });
  refresh();
})(typeof window === 'object' ? window : globalThis);
