// Test this repository by default; pass additional HTML paths explicitly.
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const paths = ['web-ecava/ecava_machine_control.html', ...process.argv.slice(2)];
for (const path of paths) {
  const html = fs.readFileSync(path, 'utf8');
  const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
  new vm.Script(script); // Check the complete JavaScript, including startup.
  const elements = {};
  const context = vm.createContext({
    document: {
      getElementById: id => elements[id] || (elements[id] = {textContent: '', getAttribute: () => null}),
      querySelectorAll: () => [],
    },
  });
  vm.runInContext(script.slice(0, script.lastIndexOf("document.addEventListener('input'")), context);
  const state = {devices: {
    electricity_meter: {connected: true, values: {power_kw: 12.5, energy_total_kwh: 1234.56, energy_today_kwh: null}},
    vfd_infeed: {connected: true, values: {motor_rpm: 1450, frequency_hz: 50}},
    vfd_pocket: {connected: true, values: {motor_rpm: 1450, frequency_hz: 50, pockets_per_min: null}},
  }};
  context.consumeState(state);
  context.render();
  assert.equal(context.latest.electricity_meter_energy_total_kwh, 1234.56);
  assert.equal(context.safeValue(context.latest.electricity_meter_energy_today_kwh), '--');
  assert.equal(context.latest.vfd_infeed_motor_rpm, 1450);
  assert.equal(context.latest.vfd_pocket_frequency_hz, 50);
  assert.equal(context.safeValue(context.latest.vfd_pocket_pockets_per_min), '--');
  assert.match(elements['pocket-calibration'].textContent, /calibration/);
  state.devices.vfd_pocket.values.pockets_per_min = 174;
  context.consumeState(state);
  context.render();
  assert.equal(context.latest.vfd_pocket_pockets_per_min, 174);
  assert.equal(elements['pocket-calibration'].textContent, '');
  state.devices.vfd_pocket.connected = false;
  context.consumeState(state);
  assert.equal(context.latest.vfd_pocket_pockets_per_min, null);
  assert.equal(context.latest.vfd_pocket_motor_rpm, null);
  context.consumeState({devices: {}});
  assert.equal(context.latest.electricity_meter_energy_total_kwh, null);
  assert.equal(context.latest.vfd_infeed_connected, 0);
  assert.equal(context.latest.vfd_infeed_frequency_hz, null);
  assert.equal(context.CIP.length, 4);
  assert.equal(context.CIP[3].name, 'Water Intake');
  context.consumeState({rpi: {cip: {water_intake: {enable: true, on_ms: 1250, off_ms: 10000, output: true}}}});
  assert.equal(context.latest.cip_water_intake_on_actual, 1.25);
  assert.equal(context.latest.cip_water_intake_enable_actual, 1);
  assert.equal(context.latest.cip_water_intake_output, 1);
  const command = context.commandFromKey('cip_water_intake_on_cmd', 2.5);
  assert.equal(command.device, 'water_intake');
  assert.equal(command.target, 'cip');
  assert.equal(command.parameters.on_ms, 2500);
  assert.equal(context.commandFromKey('cip_water_intake_enable_cmd', 0).parameters.enable, 0);
  console.log(path + ': telemetry and Water Intake command checks passed');
}
