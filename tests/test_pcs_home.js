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
