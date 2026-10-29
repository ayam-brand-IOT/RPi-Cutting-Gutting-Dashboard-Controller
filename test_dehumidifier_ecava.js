// Exercise both deployment pages with the collector's HSCS-380 JSON contract.
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
for (const path of ['web-ecava/ecava_machine_control.html', '../Fish-AWS/ecava_machine_control.html']) {
  const html = fs.readFileSync(path, 'utf8');
  const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
  new vm.Script(script);
  const elements = {};
  const context = vm.createContext({document: {
    getElementById(id) { return elements[id] ||= {textContent: '', getAttribute() { return null; }, classList: {toggle() {}}}; },
    querySelectorAll() { return []; },
  }});
  vm.runInContext(script.slice(0, script.lastIndexOf("document.addEventListener('input'")), context);
  const text = name => elements['dehumidifier-' + name].textContent;
  const device = {connected: true, values: {
    temperature_c: 30, humidity_pct: 56, humidity_start_pct: 82, humidity_stop_pct: 70,
    diagnostic_raw: 0, running: null, fault_code: null, alert_level: 'unknown',
    alert_message: 'Diagnostic register 2 = 0; alarm mapping and operating status unconfirmed.',
  }};
  function show(d) { context.consumeState({devices: d ? {dehumidifier: d} : {}}); context.render(); }
  show(device);
  assert.equal(text('connection'), 'ONLINE');
  assert.equal(text('humidity'), '56.0');
  assert.equal(text('temperature'), '30.0');
  assert.equal(text('start'), 82);
  assert.equal(text('stop'), 70);
  assert.equal(text('diagnostic'), '0 (0x0000)');
  assert.equal(text('status'), 'Unknown');
  assert.match(text('alert'), /unconfirmed/);
  assert.doesNotMatch(text('alert'), /No active alerts/);
  device.values.diagnostic_raw = 12;
  device.values.alert_level = 'warning';
  device.values.alert_message = 'Diagnostic register 2 = 12 (0x000C); meaning unconfirmed.';
  show(device);
  assert.equal(text('diagnostic'), '12 (0x000C)');
  assert.equal(elements['dehumidifier-alert'].className, 'device-alert warning');
  assert.equal(text('status'), 'Unknown');
  device.values.alert_message = '<img src=x onerror=alert(1)>';
  show(device);
  assert.equal(text('alert'), device.values.alert_message); // assigned as text, never HTML
  device.connected = false;
  show(device);
  for (const field of ['humidity', 'temperature', 'start', 'stop', 'diagnostic']) assert.equal(text(field), '--');
  assert.equal(text('connection'), 'OFFLINE');
  assert.equal(elements['dehumidifier-alert'].className, 'device-alert');
  show();
  assert.equal(text('connection'), 'UNAVAILABLE');
  device.connected = true;
  show(device);
  context.getTag = () => '{invalid';
  context.readAllTags();
  assert.equal(text('humidity'), '--');
  assert.equal(text('diagnostic'), '--');
  assert.equal(text('connection'), 'UNAVAILABLE');
  console.log(path + ': HSCS-380 readings, unknown status, warnings and disconnect checks passed');
}
