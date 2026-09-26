// Test tooling only: the PCS appliance does not need Node.js.
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = path.join(__dirname, '../web/pcs-home/js/pcs.js');
const {viewModel, safeURL} = require(source);

test('Ethernet is named Starlink only with available dish telemetry', () => {
  const network = {uplink_type: 'Starlink', uplinks: [{id: 'starlink', name: 'Starlink', type: 'ethernet', active: true}]};
  for (const starlink of [undefined, {configured: false}, {configured: true, available: false}, {configured: true, available: true}]) {
    const expected = starlink?.configured && starlink?.available ? 'Starlink' : 'Ethernet WAN';
    const model = viewModel({network, starlink});
    assert.equal(model.uplink, expected);
    assert.ok(model.wanRows[0].startsWith(expected + ':'));
  }
  assert.equal(viewModel({network: {...network, offline: true}}).uplink, 'Offline');
  assert.equal(viewModel({network: {uplink_type: 'Cellular'}, starlink: {configured: true, available: true}}).uplink, 'Cellular');
});

test('WAN totals and breakdown preserve unknowns and measured zero', () => {
  const model = viewModel({network: {usage_summary: 'Down 1 MB / Up 2 MB / Total 3 MB', uplinks: [null, {name: 'Starlink', state: 'healthy', active: true, usage: {rx_bytes: 0, tx_bytes: 1073741824, total_bytes: 1073741824}}]}});
  assert.match(model.wanUsage, /Total 3 MB/);
  assert.match(model.wanBreakdown, /Starlink: healthy \/ active/);
  assert.match(model.wanBreakdown, /Down 0.000 MB/);
  assert.match(model.wanBreakdown, /Up 1073.742 MB/);
  assert.equal(viewModel({}).wanUsage, 'Unavailable');
});

test('missing, null and malformed optional sections never become measured zero', () => {
  for (const data of [null, {}, [], {system:null, power:null, aprs:null, meshtastic:[], network:null}, {power:{configured:true,input_online:true,input_power:null}}]) {
    const model = viewModel(data);
    assert.equal(model.power, 'Unavailable');
    assert.equal(model.clients, 'Unavailable');
    assert.equal(model.state, null);
  }
});
test('power needs an online sensor and a finite reading, including genuine zero', () => {
  for (const [reading, expected] of [[18.42,'18.4 W'],[0,'0.0 W'],['18.4 W','18.4 W'],[null,'Unavailable'],[NaN,'Unavailable'],[Infinity,'Unavailable'],['N/A','Unavailable']]) {
    assert.equal(viewModel({power:{configured:true,input_online:true,input_power:reading}}).power, expected);
  }
  assert.equal(viewModel({power:{configured:true,input_online:false,input_power:18}}).power, 'Unavailable');
  assert.equal(viewModel({power:{configured:false,input_online:true,input_power:18}}).power, 'Unavailable');
});
test('existing health states and offline semantics are preserved', () => {
  for (const [overall, expected] of [['ok','● OK'],['warn','▲ WARN'],['bad','✕ BAD']]) {
    assert.equal(viewModel({overall}).health, expected);
    assert.equal(viewModel({overall,offline:true}).health, expected+' - OFFLINE');
  }
  assert.equal(viewModel({overall:'ok',network:{internet_available:false}}).state,'ok');
  assert.equal(viewModel({overall:'constructor'}).state,null);
});

test('input voltage and GPS details preserve unknown readings and real zero', () => {
  const model=viewModel({power:{configured:true,input_online:true,input_voltage:23.956},gnss:{coordinates:'41.1, -83.2',grid_square:'EN81jb'}});
  assert.equal(model.voltage,'23.96 V');
  assert.equal(model.coordinates,'41.1, -83.2');
  assert.equal(model.grid,'EN81jb');
  for (const reading of [null,NaN,Infinity]) assert.equal(viewModel({power:{configured:true,input_online:true,input_voltage:reading}}).voltage,'Unavailable');
  assert.equal(viewModel({power:{configured:true,input_online:true,input_voltage:0}}).voltage,'0.00 V');
  assert.equal(viewModel({network:{connected_client_count:2}}).clients,'Unavailable');
});
test('optional services remain optional and clients are validated', () => {
  const model=viewModel({aprs:{configured:false},meshtastic:{configured:false},pistar:{configured:false},network:{ap_client_count:0}});
  assert.equal(model.aprs,'Not configured');assert.equal(model.mesh,'Not configured');assert.equal(model.pistarURL,null);assert.equal(model.clients,'0');
  for (const value of [-1,1.5,null,'4',{}]) assert.equal(viewModel({network:{ap_client_count:value}}).clients,'Unavailable');
});
test('Pi-Star links reject script schemes and embedded credentials', () => {
  for (const value of ['javascript:alert(1)','data:text/html,test','https://user:pass@pcs.local/',null,'//example.com']) assert.equal(safeURL(value),null);
  assert.equal(safeURL('http://10.42.0.3/'),'http://10.42.0.3/');
  assert.equal(viewModel({pistar:{configured:false,url:'http://10.42.0.3/'}}).pistarURL,null);
});

function harness(responses) {
  class Element {
    constructor(dataset={}){this.dataset=dataset;this.textContent='';this.children=[];this.hidden=false;this.attributes={};}
    replaceChildren(){this.children=[];}
    appendChild(value){this.children.push(value);}
    setAttribute(key,value){this.attributes[key]=value;}
    removeAttribute(key){delete this.attributes[key];}
  }
  const nodes=Object.fromEntries(['health','health-caption','status-note','alerts','pistar-tile','pistar-open'].map(key=>[key,new Element()]));
  const readings=['power','clients','aprs','mesh'].map(field=>new Element({field}));
  const timers=new Map();let timerId=0,calls=0;
  const document={hidden:false,getElementById:id=>nodes[id],querySelectorAll:selector=>selector==='[data-field]'?readings:[],createElement:()=>new Element(),addEventListener(){}};
  const window={document};
  const context={window,URL,AbortController,setTimeout:(fn,ms)=>{timers.set(++timerId,{fn,ms});return timerId;},clearTimeout:id=>timers.delete(id),fetch:async(url,options)=>{assert.equal(url,'/api/public-status');assert.equal(options.credentials,'omit');const response=responses[calls++];if(response instanceof Error)throw response;return {ok:true,json:async()=>response};}};
  vm.runInNewContext(fs.readFileSync(source,'utf8'),context);
  return {nodes,readings,timers,get calls(){return calls;}};
}
const settled=()=>new Promise(resolve=>setImmediate(resolve));
test('failed polling preserves readings, marks them stale and recovers', async()=>{
  const healthy={overall:'ok',power:{configured:true,input_online:true,input_power:18.4}};
  const ui=harness([healthy,new Error('backend down'),healthy]);await settled();
  assert.equal(ui.readings[0].textContent,'18.4 W');
  await [...ui.timers.values()].find(t=>t.ms===8000).fn();await settled();
  assert.equal(ui.readings[0].textContent,'18.4 W');
  assert.equal(ui.nodes['status-note'].dataset.stale,'true');
  assert.match(ui.nodes.health.textContent,/unavailable/);
  await [...ui.timers.values()].find(t=>t.ms===8000).fn();await settled();
  assert.equal(ui.nodes.health.textContent,'● OK');
  assert.equal(ui.nodes['status-note'].dataset.stale,'false');
});
test('first request failure keeps service navigation intact',async()=>{
  const ui=harness([new Error('offline')]);await settled();
  assert.match(ui.nodes['status-note'].textContent,/Service links remain available/);
  assert.equal(ui.nodes['pistar-tile'].attributes.href,undefined);
});
test('alert strings are rendered as text without HTML insertion',async()=>{
  const ui=harness([{overall:'warn',alerts:[{severity:'warn',component:'<img src=x>',message:'<script>bad()</script>'}]}]);await settled();
  assert.match(ui.nodes.alerts.children[0].textContent,/<script>/);
  assert.equal(ui.nodes.alerts.children[0].children.length,0);
});

test('Starlink branch distinguishes uncommissioned, failed and measured zero', () => {
  assert.equal(viewModel({}).starlinkPower, 'Not commissioned');
  assert.equal(viewModel({power: {configured: true, starlink_configured: true, starlink_online: false, starlink_power: 40}}).starlinkPower, 'Unavailable');
  const live = viewModel({power: {configured: true, starlink_configured: true, starlink_online: true, starlink_power: 0, starlink_energy_since_boot_wh: 1.25}});
  assert.equal(live.starlinkPower, '0.0 W');
  assert.equal(live.starlinkEnergy, '1.250 Wh');
});

test('Starlink telemetry tab keeps missing and stale metrics unavailable', () => {
  assert.equal(viewModel({starlink: {configured: false}}).starlinkState, 'Not commissioned');
  const stale = viewModel({starlink: {configured: true, available: false, state: 'CONNECTED', latency_ms: 10}});
  assert.equal(stale.starlinkState, 'Unavailable');
  assert.equal(stale.starlinkLatency, 'Unavailable');
  const model = viewModel({starlink: {configured: true, available: true, state: 'CONNECTED', latency_ms: 0, downlink_bps: 12500000, packet_loss_percent: 0}});
  assert.equal(model.starlinkLatency, '0.0 ms');
  assert.equal(model.starlinkDown, '12.5 Mbps');
  assert.equal(model.starlinkLoss, '0.0%');
  assert.equal(model.starlinkUptime, 'Unavailable');
});


test('source-dependent aggregate preserves unavailable totals', () => {
  const model = viewModel({power: {configured: true, input_online: true, input_power: 20, total_power: 50,
    total_charge_since_boot_mah: 670, total_energy_since_boot_wh: 8, dc_source: 'battery', battery_remaining_wh: 492}});
  assert.equal(model.power, '50.0 W');
  assert.equal(model.powerEnergy, '8.0');
  assert.equal(model.batteryRemaining, '492.0');
  assert.equal(model.dcSource, 'Battery');
  assert.equal(viewModel({power: {configured: true, input_online: true, input_power: 20, total_power: null}}).power, 'Unavailable');
});

test('power tab formats every commissioned rail and both 12V accounting views', () => {
  const model = viewModel({power: {
    configured: true, dc_source: 'power_supply', shutdown_armed: false,
    input_online: true, input_voltage: 23.956, input_current: 0.875, input_power: 20.96,
    input_charge_since_boot_mah: 123.4, input_energy_since_boot_wh: 2.96,
    rail_12v_online: true, rail_12v_voltage: 12.418, rail_12v_current: 1.115,
    rail_12v_distribution_power: 13.846, rail_12v_power: 6.491,
    rail_12v_distribution_energy_since_boot_wh: 4.25, rail_12v_energy_since_boot_wh: 2.11,
    rail_5v_online: true, rail_5v_voltage: 5.234, rail_5v_current: 1.406,
    rail_5v_power: 7.355, rail_5v_charge_since_boot_mah: 198.7, rail_5v_energy_since_boot_wh: 1.04,
    starlink_configured: true, starlink_online: true, starlink_voltage: 23.951,
    starlink_current: 0, starlink_power: 0, starlink_charge_since_boot_mah: 0.1,
    starlink_energy_since_boot_wh: 0.184,
  }});
  assert.equal(model.dcSource, 'Power Supply');
  assert.equal(model.inputPower, '21.0 W');
  assert.equal(model.inputVoltage, '23.96 V');
  assert.equal(model.inputCurrent, '0.875 A');
  assert.equal(model.inputEnergy, '2.960 Wh');
  assert.equal(model.rail12DistributionPower, '13.8 W');
  assert.equal(model.rail12OnlyPower, '6.5 W');
  assert.equal(model.rail12DistributionEnergy, '4.250 Wh this boot');
  assert.equal(model.rail12OnlyEnergy, '2.110 Wh this boot');
  assert.equal(model.rail5Power, '7.4 W');
  assert.equal(model.starlinkPower, '0.0 W');
  assert.equal(model.starlinkCurrent, '0.000 A');
});

test('public power tab is read-only and keeps the requested rail order', () => {
  const html = fs.readFileSync(path.join(__dirname, '../web/pcs-home/power/index.html'), 'utf8');
  const headings = ['PCS Input', 'Rail 12V', 'Rail 5V', 'Starlink Passthrough'];
  const positions = headings.map(value => html.indexOf('>' + value + '<'));
  assert.ok(positions.every(value => value >= 0));
  assert.deepEqual([...positions].sort((a, b) => a - b), positions);
  assert.doesNotMatch(html, /power-settings|Change DC source|Set Battery/);
  assert.match(html, /This page is read-only/);
  const rail12 = html.slice(html.indexOf('<h2>Rail 12V</h2>'), html.indexOf('<h2>Rail 5V</h2>'));
  const labels = ['POWER', 'VOLTAGE', 'CURRENT', 'CONSUMED THIS BOOT'];
  const labelPositions = labels.map(value => rail12.indexOf('>' + value + '<'));
  assert.ok(labelPositions.every(value => value >= 0));
  assert.deepEqual([...labelPositions].sort((a, b) => a - b), labelPositions);
  assert.equal((rail12.match(/metric-label/g) || []).length, 4);
  assert.match(rail12, /5V included/);
  assert.match(rail12, /excluding 5V/);
});

test('overview service grid and public admin links follow the field layout', () => {
  const root = path.join(__dirname, '../web/pcs-home');
  const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
  const headings = ['Documentation', 'Status', 'OpenWrt', 'Pi-Star', 'Meshtastic', 'APRS'];
  const positions = headings.map(value => html.indexOf('<h3>' + value));
  assert.ok(positions.every(value => value >= 0));
  assert.deepEqual([...positions].sort((a, b) => a - b), positions);
  for (const removed of ['<h3>Starlink', '<h3>Files', '<h3>Cockpit', '<h3>Admin']) assert.doesNotMatch(html, new RegExp(removed));
  assert.match(html, /Documentation[^]*file shares/i);
  for (const relative of ['index.html', 'docs/index.html', 'files/index.html', 'pistar/index.html', 'power/index.html', 'radio/index.html', 'starlink/index.html']) {
    const page = fs.readFileSync(path.join(root, relative), 'utf8');
    const footer = page.slice(page.indexOf('<footer'));
    assert.match(footer, /href="\/admin\/"[^>]*>Admin Login/);
    assert.doesNotMatch(page.slice(0, page.indexOf('<footer')), /class="admin-link"/);
  }
  const docs = fs.readFileSync(path.join(root, 'docs/index.html'), 'utf8');
  assert.match(docs, /PCS-Share/);
  assert.match(docs, /PCS-Backup/);
  assert.match(docs, /Start PCS/);
  assert.match(docs, /Portable Comm Server/);
  assert.match(docs, /90-second controlled shutdown/);
  assert.match(docs, /Degraded and offline operation/);
  assert.match(docs, /GPS time[^]*Internet time[^]*RTC holdover/);
  assert.match(docs, /RAK4631/);
  assert.match(docs, /Starlink Mini/);
  assert.doesNotMatch(docs, /unplug[^]*Ethernet/i);
  assert.doesNotMatch(docs, /<code>pi<\/code>/);
  assert.doesNotMatch(docs, /Cockpit/);
});
