import React, { useCallback, useEffect, useState } from 'react';
import {
  Alert,
  NativeModules,
  PermissionsAndroid,
  Platform,
  Pressable,
  ScrollView,
  StatusBar,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { SafeAreaProvider, SafeAreaView } from 'react-native-safe-area-context';

type State = {
  running: boolean;
  status: string;
  completed: number;
  selectedPc: string;
  url: string;
  hasToken: boolean;
};
type Device = { name: string; address: string };
const bridge = NativeModules.BlueDrop as {
  state(): Promise<State>;
  paired(): Promise<Device[]>;
  save(url: string, token: string, pc: string): Promise<boolean>;
  testBackend(): Promise<string>;
  start(): Promise<boolean>;
  stop(): Promise<boolean>;
  openSettings(): Promise<boolean>;
};
const initial: State = {
  running: false,
  status: 'Stopped',
  completed: 0,
  selectedPc: '',
  url: '',
  hasToken: false,
};

async function permission() {
  if (Platform.OS !== 'android') {
    throw new Error('Android only');
  }
  if (Number(Platform.Version) >= 31) {
    const value = await PermissionsAndroid.request(
      PermissionsAndroid.PERMISSIONS.BLUETOOTH_CONNECT,
    );
    if (value !== PermissionsAndroid.RESULTS.GRANTED) {
      throw new Error('Allow Nearby devices to connect your paired PC.');
    }
  }
  if (Number(Platform.Version) >= 33) {
    await PermissionsAndroid.request(
      PermissionsAndroid.PERMISSIONS.POST_NOTIFICATIONS,
    );
  }
}

export default function App() {
  const [state, setState] = useState(initial);
  const [url, setUrl] = useState('');
  const [token, setToken] = useState('');
  const [pc, setPc] = useState('');
  const [devices, setDevices] = useState<Device[]>([]);
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState('');
  const refresh = useCallback(async () => {
    const next = await bridge.state();
    setState(next);
    return next;
  }, []);
  useEffect(() => {
    let live = true;
    bridge
      .state()
      .then(next => {
        if (live) {
          setState(next);
          setUrl(next.url);
          setPc(next.selectedPc);
        }
      })
      .catch(e => {
        if (live) {
          setFeedback(String(e));
        }
      });
    const timer = setInterval(() => {
      if (live) {
        refresh().catch(() => {});
      }
    }, 1000);
    return () => {
      live = false;
      clearInterval(timer);
    };
  }, [refresh]);
  const run = async (action: () => Promise<unknown>) => {
    setBusy(true);
    setFeedback('');
    try {
      await action();
      await refresh();
    } catch (e) {
      Alert.alert('BlueDrop AI', e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  const save = async () => {
    await permission();
    await bridge.save(url, token, pc);
    setToken('');
  };
  const button = (
    label: string,
    action: () => Promise<unknown>,
    secondary = false,
    disabled = false,
  ) => (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={label}
      disabled={busy || disabled}
      onPress={() => run(action)}
      style={[
        styles.button,
        secondary && styles.secondary,
        (busy || disabled) && styles.disabled,
      ]}
    >
      <Text style={[styles.buttonText, secondary && styles.secondaryText]}>
        {label}
      </Text>
    </Pressable>
  );
  return (
    <SafeAreaProvider>
      <SafeAreaView style={styles.screen}>
        <StatusBar barStyle="light-content" backgroundColor="#091522" />
        <ScrollView
          contentContainerStyle={styles.content}
          keyboardShouldPersistTaps="handled"
        >
          <Text style={styles.eyebrow}>BLUETOOTH × AI</Text>
          <Text style={styles.title}>BlueDrop AI</Text>
          <Text style={styles.subtitle}>
            Your phone brings AI to your offline PC.
          </Text>
          <View style={styles.card}>
            <View style={styles.row}>
              <View style={[styles.dot, state.running && styles.on]} />
              <Text style={styles.heading}>
                {state.running ? 'Bridge is on' : 'Bridge is off'}
              </Text>
            </View>
            <Text accessibilityLiveRegion="polite" style={styles.status}>
              {state.status}
            </Text>
            <Text style={styles.muted}>
              {state.completed} replies delivered this app session
            </Text>
            {button(
              state.running ? 'Stop bridge' : 'Start bridge',
              async () => {
                if (state.running) {
                  await bridge.stop();
                } else {
                  await save();
                  await bridge.start();
                }
              },
            )}
          </View>
          <View style={styles.card}>
            <Text style={styles.heading}>1. Connect your backend</Text>
            <Text style={styles.label}>Backend URL (HTTP or HTTPS)</Text>
            <TextInput
              accessibilityLabel="Backend URL"
              editable={!state.running}
              value={url}
              onChangeText={setUrl}
              autoCapitalize="none"
              autoCorrect={false}
              keyboardType="url"
              placeholder="http://192.168.0.103:8000"
              placeholderTextColor="#778aa0"
              style={styles.input}
            />
            <Text style={styles.label}>Bridge token</Text>
            <TextInput
              accessibilityLabel="Bridge token"
              editable={!state.running}
              secureTextEntry
              value={token}
              onChangeText={setToken}
              autoCapitalize="none"
              autoCorrect={false}
              placeholder={
                state.hasToken
                  ? 'Saved securely • leave blank to keep'
                  : 'BRIDGE_TOKEN from backend .env'
              }
              placeholderTextColor="#778aa0"
              style={styles.input}
            />
            <Text style={styles.muted}>
              The Groq API key stays on your backend.
            </Text>
            <Text style={[styles.heading, styles.sectionGap]}>
              2. Choose your paired PC
            </Text>
            {button(
              'Load paired devices',
              async () => {
                await permission();
                setDevices(await bridge.paired());
              },
              true,
              state.running,
            )}
            {devices.map(d => (
              <Pressable
                key={d.address}
                accessibilityRole="radio"
                accessibilityState={{ checked: pc === d.address }}
                disabled={state.running}
                onPress={() => setPc(d.address)}
                style={[styles.device, pc === d.address && styles.selected]}
              >
                <Text style={styles.deviceText}>{d.name}</Text>
                <Text style={styles.muted}>
                  {d.address}
                  {pc === d.address ? '  • selected' : ''}
                </Text>
              </Pressable>
            ))}
            {pc ? (
              <Text style={styles.muted}>Selected PC: {pc}</Text>
            ) : (
              <Text style={styles.muted}>
                Pair the PC in Android Bluetooth Settings first.
              </Text>
            )}
            {button('Bluetooth settings', () => bridge.openSettings(), true)}
            {button(
              'Save & test backend',
              async () => {
                await save();
                const result = JSON.parse(await bridge.testBackend());
                setFeedback(`Connected • ${result.model}`);
              },
              true,
              state.running,
            )}
            {feedback ? <Text style={styles.success}>{feedback}</Text> : null}
          </View>
          <View style={styles.card}>
            <Text style={styles.heading}>3. Chat from your PC</Text>
            <Text style={styles.body}>
              Start the bridge, then run receiver.py in Windows Terminal. Type
              your message and press Enter. Replies appear there and save as TXT
              files.
            </Text>
            <Text selectable style={styles.code}>
              py -3 receiver.py --setup{'\n'}py -3 receiver.py --chat
            </Text>
            <Text style={styles.muted}>
              Keep mobile internet and Bluetooth on. The Python client must
              remain running. Start the bridge again after reboot or force-stop.
            </Text>
          </View>
          <Text style={styles.footer}>
            PC • Bluetooth • Phone • Your backend • Groq
          </Text>
        </ScrollView>
      </SafeAreaView>
    </SafeAreaProvider>
  );
}
const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: '#091522' },
  content: { padding: 22, paddingBottom: 40, gap: 18 },
  eyebrow: {
    color: '#63e5c4',
    fontSize: 12,
    fontWeight: '700',
    letterSpacing: 2,
  },
  title: { color: '#f3f7fc', fontSize: 34, fontWeight: '800' },
  subtitle: { color: '#a8b9cb', fontSize: 16, marginTop: -10 },
  card: {
    backgroundColor: '#132435',
    padding: 20,
    borderRadius: 20,
    borderWidth: 1,
    borderColor: '#263c50',
    gap: 12,
  },
  row: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  dot: { width: 10, height: 10, borderRadius: 5, backgroundColor: '#8192a4' },
  on: { backgroundColor: '#63e5c4' },
  heading: { color: '#f3f7fc', fontSize: 19, fontWeight: '700' },
  status: { color: '#c8d8e8', fontSize: 16 },
  muted: { color: '#9daec1', fontSize: 13, lineHeight: 20 },
  button: {
    backgroundColor: '#63e5c4',
    borderRadius: 12,
    padding: 15,
    alignItems: 'center',
  },
  buttonText: { color: '#09241e', fontSize: 16, fontWeight: '700' },
  secondary: { backgroundColor: '#20394b' },
  secondaryText: { color: '#d7e9f6' },
  disabled: { opacity: 0.45 },
  label: { color: '#c8d8e8', fontSize: 14 },
  input: {
    color: '#f3f7fc',
    backgroundColor: '#0b1b29',
    borderColor: '#365066',
    borderWidth: 1,
    borderRadius: 10,
    padding: 13,
    fontSize: 15,
  },
  device: {
    borderColor: '#365066',
    borderWidth: 1,
    borderRadius: 10,
    padding: 12,
  },
  selected: { borderColor: '#63e5c4', backgroundColor: '#183f3d' },
  deviceText: { color: '#f3f7fc', fontSize: 16 },
  success: { color: '#63e5c4' },
  body: { color: '#c8d8e8', lineHeight: 23, fontSize: 15 },
  code: {
    color: '#63e5c4',
    fontFamily: 'monospace',
    lineHeight: 24,
    backgroundColor: '#0b1b29',
    padding: 12,
    borderRadius: 8,
  },
  footer: { color: '#9daec1', textAlign: 'center', fontSize: 12 },
  sectionGap: { marginTop: 12 },
});
