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
  function reading(value, suffix, decimals) {
    return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value.toFixed(decimals) + ' ' + suffix : 'Unavailable';
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
    const starlink = object(data.starlink);
    const metric = (value, decimals = 1, fallback = 'Unavailable') => typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value.toFixed(decimals) : fallback;
    const slReady = starlink.configured === true && starlink.available === true;
    const slMetric = (key, suffix, scale = 1) => slReady && typeof starlink[key] === 'number' && Number.isFinite(starlink[key]) && starlink[key] >= 0 ? (starlink[key] / scale).toFixed(1) + suffix : 'Unavailable';
    const state = Object.hasOwn(states, data.overall) ? data.overall : null;
    const offline = data.offline === true || network.offline === true;
    const inputWatts = power.configured === true ? watts(Object.hasOwn(power, 'total_power') ? power.total_power : power.input_online === true ? power.input_power : null) : 'Unavailable';
    const clients = typeof network.ap_client_count === 'number' && Number.isInteger(network.ap_client_count) && network.ap_client_count >= 0 ? String(network.ap_client_count) : 'Unavailable';
    const uplinks = Array.isArray(network.uplinks) ? network.uplinks.filter(u => u && typeof u === 'object' && !Array.isArray(u)).slice(0, 32) : [];
    const wanName = u => u.type === 'ethernet' ? (u.id === 'starlink' && slReady ? 'Starlink' : 'Ethernet WAN') : text(u.name, 'WAN');
    const activeWAN = uplinks.find(u => u.active === true);
    const wanRows = uplinks.map(u => {
      const usage = object(u.usage);
      const bytes = key => typeof usage[key] === 'number' && Number.isFinite(usage[key]) && usage[key] >= 0 ? (usage[key] / 1_000_000).toFixed(3) + ' MB' : 'Unavailable';
      const traffic = Object.keys(usage).length ? `Down ${bytes('rx_bytes')} / Up ${bytes('tx_bytes')} / Total ${bytes('total_bytes')}` : 'Traffic unavailable';
      return `${wanName(u)}: ${text(u.state, 'unknown')}${u.active === true ? ' / active' : ''} · ${traffic}`;
    });
    return {
      powerCharge: metric(power.total_charge_since_boot_mah, 0), powerEnergy: metric(power.total_energy_since_boot_wh),
      dcSource: power.dc_source === 'battery' ? 'Battery' : power.dc_source === 'power_supply' ? 'Power Supply' : 'Unknown',
      batteryCapacity: metric(power.battery_capacity_wh, 1, 'Not entered'), batteryRemaining: metric(power.battery_remaining_wh, 1, 'Not available'),
      batteryPercent: text(power.battery_remaining_percent, 'Not available'),
      batteryWarning: power.battery_capacity_warning === true ? 'Estimated capacity is at or below 10%. Voltage protection remains authoritative.' : 'Capacity is an estimate; voltage protection operates independently.',
      shutdownProtection: power.shutdown_armed === true ? 'Enabled' : power.shutdown_armed === false ? 'Disabled' : 'Unknown',
      inputPower: power.configured === true && power.input_online === true ? watts(power.input_power) : 'Unavailable',
      inputVoltage: power.configured === true && power.input_online === true ? reading(power.input_voltage, 'V', 2) : 'Unavailable',
      inputCurrent: power.configured === true && power.input_online === true ? reading(power.input_current, 'A', 3) : 'Unavailable',
      inputEnergy: power.configured === true && power.input_online === true ? reading(power.input_energy_since_boot_wh, 'Wh', 3) : 'Unavailable',
      inputCharge: power.configured === true && power.input_online === true ? reading(power.input_charge_since_boot_mah, 'mAh', 1) : 'Unavailable',
      rail12Voltage: power.configured === true && power.rail_12v_online === true ? reading(power.rail_12v_voltage, 'V', 2) : 'Unavailable',
      rail12Current: power.configured === true && power.rail_12v_online === true ? reading(power.rail_12v_current, 'A', 3) : 'Unavailable',
      rail12DistributionPower: power.configured === true && power.rail_12v_online === true ? watts(power.rail_12v_distribution_power) : 'Unavailable',
      rail12DistributionEnergy: power.configured === true && power.rail_12v_online === true ? reading(power.rail_12v_distribution_energy_since_boot_wh, 'Wh this boot', 3) : 'Unavailable',
      rail12OnlyPower: power.configured === true && power.rail_12v_online === true ? watts(power.rail_12v_power) : 'Unavailable',
      rail12OnlyEnergy: power.configured === true && power.rail_12v_online === true ? reading(power.rail_12v_energy_since_boot_wh, 'Wh this boot', 3) : 'Unavailable',
      rail5Power: power.configured === true && power.rail_5v_online === true ? watts(power.rail_5v_power) : 'Unavailable',
      rail5Voltage: power.configured === true && power.rail_5v_online === true ? reading(power.rail_5v_voltage, 'V', 2) : 'Unavailable',
      rail5Current: power.configured === true && power.rail_5v_online === true ? reading(power.rail_5v_current, 'A', 3) : 'Unavailable',
      rail5Energy: power.configured === true && power.rail_5v_online === true ? reading(power.rail_5v_energy_since_boot_wh, 'Wh', 3) : 'Unavailable',
      rail5Charge: power.configured === true && power.rail_5v_online === true ? reading(power.rail_5v_charge_since_boot_mah, 'mAh', 1) : 'Unavailable',
      starlinkState: starlink.configured === false ? 'Not commissioned' : slReady ? text(starlink.state) : 'Unavailable',
      starlinkLatency: slMetric('latency_ms', ' ms'), starlinkLoss: slMetric('packet_loss_percent', '%'),
      starlinkObstruction: slMetric('obstruction_percent', '%'), starlinkDown: slMetric('downlink_bps', ' Mbps', 1e6),
      starlinkUp: slMetric('uplink_bps', ' Mbps', 1e6), starlinkUptime: slMetric('uptime_seconds', ' s'),
      starlinkAge: slMetric('sample_age_seconds', ' s'), starlinkAlerts: slReady ? text(starlink.alerts_summary) : 'Unavailable',
      state, health: state ? states[state] + (offline ? ' - OFFLINE' : '') : '— Status unavailable',
      callsign: text(aprs.callsign, 'PCS FIELD STATION'), localTime: text(system.local_time), uptime: text(system.uptime),
      uplink: offline || network.internet_available === false ? 'Offline' : activeWAN && activeWAN.type === 'ethernet' ? wanName(activeWAN) : network.uplink_type === 'Starlink' && !slReady ? 'Ethernet WAN' : text(network.uplink_type),
      wanUsage: text(network.usage_summary),
      starlinkPower: power.configured === true && power.starlink_online === true ? watts(power.starlink_power) : power.configured === true && power.starlink_configured === true ? 'Unavailable' : 'Not commissioned',
      starlinkEnergy: power.configured === true && power.starlink_online === true && typeof power.starlink_energy_since_boot_wh === 'number' && Number.isFinite(power.starlink_energy_since_boot_wh) ? power.starlink_energy_since_boot_wh.toFixed(3) + ' Wh' : 'Unavailable',
      starlinkVoltage: power.configured === true && power.starlink_online === true ? reading(power.starlink_voltage, 'V', 2) : 'Unavailable',
      starlinkCurrent: power.configured === true && power.starlink_online === true ? reading(power.starlink_current, 'A', 3) : 'Unavailable',
      starlinkCharge: power.configured === true && power.starlink_online === true ? reading(power.starlink_charge_since_boot_mah, 'mAh', 1) : 'Unavailable',
      wanRows, wanBreakdown: wanRows.join(' • ') || 'Unavailable',
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
    const wanList = doc.getElementById('wan-breakdown');
    if (wanList) {
      wanList.replaceChildren();
      (model.wanRows.length ? model.wanRows : ['Uplink details unavailable']).forEach(row => {
        const item = doc.createElement('li'); item.textContent = row; wanList.appendChild(item);
      });
    }
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
