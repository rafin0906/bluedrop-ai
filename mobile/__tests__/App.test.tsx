import React from 'react';
import { NativeModules, PermissionsAndroid, Platform } from 'react-native';
import TestRenderer, { act } from 'react-test-renderer';
jest.mock('react-native-safe-area-context', () => {
  const { View } = require('react-native');
  return { SafeAreaProvider: View, SafeAreaView: View };
});
const state = {
  running: false,
  status: 'Stopped',
  completed: 0,
  selectedPc: 'AA:BB:CC:DD:EE:FF',
  url: 'https://example.com',
  hasToken: true,
};
const api = {
  state: jest.fn(),
  paired: jest.fn().mockResolvedValue([]),
  save: jest.fn().mockResolvedValue(true),
  start: jest.fn().mockResolvedValue(true),
  stop: jest.fn().mockResolvedValue(true),
  testBackend: jest.fn().mockResolvedValue('{"model":"openai/gpt-oss-120b"}'),
  openSettings: jest.fn().mockResolvedValue(true),
};
jest.setTimeout(35000);
let renderer: TestRenderer.ReactTestRenderer;
beforeEach(() => {
  jest.useFakeTimers();
  jest.clearAllMocks();
  NativeModules.BlueDrop = api;
  api.state.mockResolvedValue(state);
  Object.defineProperty(Platform, 'OS', { get: () => 'android' });
  Object.defineProperty(Platform, 'Version', { get: () => 36 });
  jest
    .spyOn(PermissionsAndroid, 'request')
    .mockResolvedValue(PermissionsAndroid.RESULTS.GRANTED);
});
afterEach(async () => {
  if (renderer) {
    await act(async () => renderer.unmount());
  }
  jest.useRealTimers();
});
async function mount() {
  const App = require('../App').default;
  await act(async () => {
    renderer = TestRenderer.create(<App />);
  });
}
async function press(label: string) {
  const button = renderer.root.findAll(
    p =>
      p.props.accessibilityLabel === label &&
      typeof p.props.onPress === 'function',
  )[0];
  if (!button) {
    throw new Error('Missing button ' + label);
  }
  await act(async () => {
    await button.props.onPress();
  });
}
test('starts bridge with saved settings and blank token preservation', async () => {
  await mount();
  await press('Start bridge');
  expect(api.save).toHaveBeenCalledWith(state.url, '', state.selectedPc);
  expect(api.start).toHaveBeenCalledTimes(1);
});
test('tests configured backend', async () => {
  await mount();
  await press('Save & test backend');
  expect(api.testBackend).toHaveBeenCalledTimes(1);
  expect(JSON.stringify(renderer.toJSON())).toContain('openai/gpt-oss-120b');
});
test('stops bridge without saving settings', async () => {
  api.state.mockResolvedValue({ ...state, running: true });
  await mount();
  await press('Stop bridge');
  expect(api.stop).toHaveBeenCalledTimes(1);
  expect(api.save).not.toHaveBeenCalled();
});
