#!/usr/bin/env python3
"""BlueDrop AI: offline Windows terminal chat via your Android Bluetooth bridge.
Python 3.10+; standard library only. No PC internet or project dependencies.
"""
from __future__ import annotations
import argparse
import ctypes as C
import json
import os
from pathlib import Path
import re
import struct
import sys
import tempfile
import time
import uuid

if sys.platform == 'win32':
    try:
        if hasattr(sys.stdout, 'reconfigure'):
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        if hasattr(sys.stderr, 'reconfigure'):
            sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

SERVICE_UUID = "c91c315b-8c0b-487f-a640-c073e9415d55"
MAX_FRAME = 1048576
DEFAULT_CONFIG = Path(os.getenv("LOCALAPPDATA", str(Path.home()))) / "BlueDropAI" / "receiver-config.json"


class ProtocolError(Exception):
    pass


def exact(stream, count):
    result = bytearray()
    while len(result) < count:
        block = stream.recv(min(65536, count - len(result)))
        if not block:
            raise EOFError("Bluetooth connection closed before the transfer completed")
        result.extend(block)
    return bytes(result)


def send_json(stream, value):
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if not 0 < len(raw) <= MAX_FRAME:
        raise ProtocolError("Outgoing control frame too large")
    stream.sendall(struct.pack(">I", len(raw)) + raw)


def receive_json(stream):
    size = struct.unpack(">I", exact(stream, 4))[0]
    if not 0 < size <= MAX_FRAME:
        raise ProtocolError("Invalid control frame length")
    try:
        value = json.loads(exact(stream, size).decode("utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise ProtocolError("Invalid JSON frame") from exc
    if not isinstance(value, dict):
        raise ProtocolError("Expected a JSON object")
    return value



# Fixed-width Windows ABI definitions. Do not use ctypes.c_long (64-bit on Linux)
# or c_wchar (32-bit on Linux); tests validate the layout independently of the OS.
class GUID(C.Structure):
    _fields_ = [("data1", C.c_uint32), ("data2", C.c_uint16), ("data3", C.c_uint16), ("data4", C.c_ubyte * 8)]


class SOCKADDR_BTH(C.Structure):
    _pack_ = 1  # ws2bth.h explicitly includes pshpack1.h
    _fields_ = [("family", C.c_uint16), ("address", C.c_uint64), ("service", GUID), ("port", C.c_uint32)]


class SYSTEMTIME(C.Structure):
    _fields_ = [("values", C.c_uint16 * 8)]


class DEVICE_INFO(C.Structure):
    _fields_ = [("size", C.c_uint32), ("address", C.c_uint64), ("device_class", C.c_uint32),
                ("connected", C.c_int32), ("remembered", C.c_int32), ("authenticated", C.c_int32),
                ("last_seen", SYSTEMTIME), ("last_used", SYSTEMTIME), ("name", C.c_uint16 * 248)]


class SEARCH_PARAMS(C.Structure):
    _fields_ = [("size", C.c_uint32), ("authenticated", C.c_int32), ("remembered", C.c_int32),
                ("unknown", C.c_int32), ("connected", C.c_int32), ("inquiry", C.c_int32),
                ("timeout", C.c_ubyte), ("radio", C.c_void_p)]


def require_windows():
    if sys.platform != "win32":
        raise RuntimeError("Bluetooth receiver requires native Windows 10/11, not WSL. Use Windows Python.")


def address_text(value):
    raw = f"{value:012X}"
    return ":".join(raw[i:i + 2] for i in range(0, 12, 2))


def parse_address(text):
    raw = text.replace(":", "").replace("-", "")
    if not re.fullmatch(r"[0-9a-fA-F]{12}", raw):
        raise ValueError("Bluetooth address must look like AA:BB:CC:DD:EE:FF")
    return int(raw, 16)


def paired_devices():
    require_windows()
    dll = C.WinDLL("bthprops.cpl", use_last_error=True)
    first = dll.BluetoothFindFirstDevice
    first.argtypes = [C.POINTER(SEARCH_PARAMS), C.POINTER(DEVICE_INFO)]; first.restype = C.c_void_p
    nxt = dll.BluetoothFindNextDevice
    nxt.argtypes = [C.c_void_p, C.POINTER(DEVICE_INFO)]; nxt.restype = C.c_int32
    close = dll.BluetoothFindDeviceClose
    close.argtypes = [C.c_void_p]; close.restype = C.c_int32
    params = SEARCH_PARAMS(); params.size = C.sizeof(params)
    params.authenticated = params.remembered = params.connected = 1
    info = DEVICE_INFO(); info.size = C.sizeof(info)
    handle = first(C.byref(params), C.byref(info))
    if not handle:
        error = C.get_last_error()
        if error in (0, 259): return []  # ERROR_NO_MORE_ITEMS
        raise OSError(error, "Could not enumerate paired Bluetooth devices")
    results = []
    try:
        while True:
            name = bytes(info.name).decode("utf-16-le").split("\0", 1)[0]
            results.append({"name": name, "address": address_text(info.address)})
            info = DEVICE_INFO(); info.size = C.sizeof(info)
            if not nxt(handle, C.byref(info)):
                error = C.get_last_error()
                if error not in (0, 259): raise OSError(error, "Bluetooth enumeration failed")
                break
    finally:
        close(handle)
    return results


class WindowsBluetooth:
    """Native Winsock RFCOMM socket; Windows resolves the advertised service UUID."""
    def __init__(self, address, timeout=25):
        require_windows()
        self.handle = None
        self.started = False
        self.ws = C.WinDLL("Ws2_32.dll", use_last_error=True)
        sock = C.c_size_t
        signatures = {
            "WSAStartup": ([C.c_uint16, C.c_void_p], C.c_int),
            "WSACleanup": ([], C.c_int), "WSAGetLastError": ([], C.c_int),
            "socket": ([C.c_int, C.c_int, C.c_int], sock),
            "connect": ([sock, C.c_void_p, C.c_int], C.c_int),
            "closesocket": ([sock], C.c_int),
            "recv": ([sock, C.c_void_p, C.c_int, C.c_int], C.c_int),
            "send": ([sock, C.c_void_p, C.c_int, C.c_int], C.c_int),
            "ioctlsocket": ([sock, C.c_int32, C.POINTER(C.c_uint32)], C.c_int),
            "setsockopt": ([sock, C.c_int, C.c_int, C.c_void_p, C.c_int], C.c_int),
            "getsockopt": ([sock, C.c_int, C.c_int, C.c_void_p, C.POINTER(C.c_int)], C.c_int),
            "select": ([C.c_int, C.c_void_p, C.c_void_p, C.c_void_p, C.c_void_p], C.c_int),
        }
        for name, (args, result) in signatures.items():
            fn = getattr(self.ws, name); fn.argtypes = args; fn.restype = result
        try:
            wsa_data = C.create_string_buffer(512)
            code = self.ws.WSAStartup(0x0202, wsa_data)
            if code: raise OSError(code, "Winsock initialization failed")
            self.started = True
            self.handle = self.ws.socket(32, 1, 3)  # AF_BTH, SOCK_STREAM, BTHPROTO_RFCOMM
            if self.handle == C.c_size_t(-1).value:
                self.handle = None; self._error("Cannot create Bluetooth socket")
            enabled = C.c_uint32(1)
            # These constants are defined in the official Windows ws2bth.h header.
            self._check(self.ws.setsockopt(self.handle, 3, C.c_int32(0x80000001), C.byref(enabled), 4), "Cannot require authentication")
            self._check(self.ws.setsockopt(self.handle, 3, 0x00000002, C.byref(enabled), 4), "Cannot require encryption")
            nonblocking = C.c_uint32(1)
            self._check(self.ws.ioctlsocket(self.handle, C.c_int32(0x8004667E), C.byref(nonblocking)), "Cannot set connect timeout")
            remote = SOCKADDR_BTH()
            remote.family = 32; remote.address = parse_address(address)
            remote.service = GUID.from_buffer_copy(uuid.UUID(SERVICE_UUID).bytes_le)
            remote.port = 0  # ServiceClassId selects the remote RFCOMM channel via SDP.
            result = self.ws.connect(self.handle, C.byref(remote), C.sizeof(remote))
            if result == -1:
                error = self.ws.WSAGetLastError()
                if error not in (10035, 10036): raise OSError(error, "Bluetooth connect failed")
                class FD_SET(C.Structure):
                    _fields_ = [("count", C.c_uint32), ("sockets", sock * 64)]
                class TIMEVAL(C.Structure):
                    _fields_ = [("seconds", C.c_int32), ("microseconds", C.c_int32)]
                write = FD_SET(); write.count = 1; write.sockets[0] = self.handle
                exceptional = FD_SET(); exceptional.count = 1; exceptional.sockets[0] = self.handle
                wait = TIMEVAL(int(timeout), 0)
                ready = self.ws.select(0, None, C.byref(write), C.byref(exceptional), C.byref(wait))
                if ready == 0: raise TimeoutError("Bluetooth connect timed out. Is the phone server running?")
                self._check(ready, "Bluetooth connection wait failed")
                code = C.c_int(0); length = C.c_int(4)
                self._check(self.ws.getsockopt(self.handle, 0xFFFF, 0x1007, C.byref(code), C.byref(length)), "Cannot read connection result")
                if code.value: raise OSError(code.value, "Bluetooth connect failed; check pairing and phone server")
            nonblocking.value = 0
            self._check(self.ws.ioctlsocket(self.handle, C.c_int32(0x8004667E), C.byref(nonblocking)), "Cannot restore blocking mode")
            milliseconds = C.c_uint32(60000)
            for option in (0x1005, 0x1006):  # SO_SNDTIMEO / SO_RCVTIMEO
                self._check(self.ws.setsockopt(self.handle, 0xFFFF, option, C.byref(milliseconds), 4), "Cannot set transfer timeout")
        except BaseException:
            self.close(); raise

    def _error(self, message):
        raise OSError(self.ws.WSAGetLastError(), message)

    def _check(self, value, message):
        if value == -1: self._error(message)
        return value

    def recv(self, size):
        buffer = C.create_string_buffer(size)
        n = self._check(self.ws.recv(self.handle, buffer, size, 0), "Bluetooth read failed")
        return buffer.raw[:n]

    def sendall(self, data):
        offset = 0
        while offset < len(data):
            chunk = data[offset:offset + 65536]
            buffer = C.create_string_buffer(chunk, len(chunk))
            n = self._check(self.ws.send(self.handle, buffer, len(chunk), 0), "Bluetooth write failed")
            if n == 0: raise EOFError("Bluetooth connection closed")
            offset += n

    def close(self):
        if self.handle is not None:
            self.ws.closesocket(self.handle); self.handle = None
        if self.started:
            self.ws.WSACleanup(); self.started = False

    def __enter__(self): return self
    def __exit__(self, *_): self.close()


MAX_CONTEXT = 65536
MAX_ANSWER = 204800

class RemoteError(Exception):
    def __init__(self, message, retryable=False):
        super().__init__(message)
        self.retryable = retryable


def chat_session(stream, request, on_status=lambda value: None):
    send_json(stream, dict(request, type='chat', version=2))
    while True:
        reply = receive_json(stream)
        if reply.get('id') != request['id']: raise ProtocolError('Response request ID mismatch')
        kind = reply.get('type')
        if kind == 'status':
            on_status(reply.get('status', 'waiting'))
            continue
        if kind == 'error':
            raise RemoteError(str(reply.get('message', 'Bridge error')), reply.get('retryable') is True)
        if kind != 'result' or not isinstance(reply.get('answer'), str):
            raise ProtocolError('Invalid bridge response')
        if not reply['answer'].strip() or len(reply['answer'].encode('utf-8')) > MAX_ANSWER:
            raise ProtocolError('Invalid answer size')
        return reply


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.pending-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(value, f, ensure_ascii=False, indent=2)
            f.flush(); os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def save_reply(folder, reply):
    """Publish whole UTF-8 reply without overwriting existing user files (local NTFS)."""
    folder = Path(folder); folder.mkdir(parents=True, exist_ok=True)
    ident = str(uuid.UUID(reply['id']))
    target = folder / f'answer-{ident}.txt'
    raw = reply['answer'].encode('utf-8')
    if target.exists():
        if target.is_symlink() or target.read_bytes() != raw:
            raise OSError(f'Existing reply was changed. Move it elsewhere: {target}')
        return target
    fd, name = tempfile.mkstemp(prefix='.answer-', suffix='.part', dir=folder)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(raw); f.flush(); os.fsync(f.fileno())
        try: os.link(name, target)
        except FileExistsError:
            if target.is_symlink() or target.read_bytes() != raw: raise OSError('Reply filename collision')
    finally:
        Path(name).unlink(missing_ok=True)
    return target


def context_messages(history, prompt):
    if not prompt.strip(): raise ValueError('Message cannot be empty')
    if len(prompt.encode('utf-8')) > MAX_CONTEXT: raise ValueError('Message exceeds 64 KiB UTF-8')
    # Trim oldest complete turns; never split text in the middle of a Unicode character.
    messages = list(history[-38:]) + [{'role': 'user', 'content': prompt}]
    while sum(len(m['content'].encode('utf-8')) for m in messages) > MAX_CONTEXT and len(messages) > 1:
        messages = messages[2:]
    return messages


def new_request(history, prompt):
    return {'id': str(uuid.uuid4()), 'messages': context_messages(history, prompt)}


def setup(path):
    devices = paired_devices()
    if not devices: raise ValueError('Pair your Android phone in Windows Bluetooth Settings first.')
    for i, d in enumerate(devices, 1): print(f"{i}. {d['name']!r} [{d['address']}]")
    i = int(input('Select your Android PHONE number: '))
    if not 1 <= i <= len(devices): raise ValueError('Invalid selection')
    old = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    default = old.get('output', str(Path.home() / 'Downloads' / 'BlueDropAI'))
    output = input(f'Save replies folder (local NTFS) [{default}]: ').strip() or default
    output = str(Path(output).expanduser().resolve())
    Path(output).mkdir(parents=True, exist_ok=True)
    atomic_json(path, {'address': devices[i-1]['address'], 'output': output})
    print(f'Settings saved: {path}\nStart the phone bridge, then run: py -3 receiver.py --chat')


def execute_pending(config, state, state_path, connector=WindowsBluetooth):
    request = state['pending']
    previous = None
    while True:
        try:
            with connector(config['address']) as stream:
                def show_status(value):
                    nonlocal previous
                    if value != previous:
                        print(f'  [{value}]', flush=True); previous = value
                reply = chat_session(stream, request, show_status)
                target = save_reply(config['output'], reply)
                # Commit local history BEFORE acknowledging delivery.
                state['history'] = (request['messages'] + [{'role': 'assistant', 'content': reply['answer']}])[-38:]
                state['pending'] = None
                atomic_json(state_path, state)
                try: send_json(stream, {'type': 'ack', 'id': request['id']})
                except (OSError, EOFError): pass  # Reply already committed locally.
            print('\nAI: ' + printable(reply['answer']) + '\n')
            if reply.get('finish_reason') == 'length': print('[Output token limit reached; ask it to continue.]')
            print(f'[Saved: {target}]')
            return reply
        except RemoteError as exc:
            if not exc.retryable:
                state['pending'] = None; atomic_json(state_path, state)
                raise
            message = str(exc)
        except (OSError, EOFError, ProtocolError) as exc:
            message = str(exc)
            # If local save failed after a complete reply, stop rather than regenerate.
            if state.get('pending') is None: raise
        if message != previous:
            print(f'[Waiting: {printable(message)} | retry in 5s; Ctrl+C keeps pending request]')
            previous = message
        time.sleep(5)


def printable(text):
    # LLM text is data: suppress terminal escape/control sequences, retain newlines/tabs.
    return ''.join(c for c in text if c in '\n\t' or (ord(c) >= 32 and not 127 <= ord(c) <= 159))


@__import__('contextlib').contextmanager
def single_instance(path):
    """OS lock is released on crash; prevents two clients racing on one state file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as f:
        f.seek(0); f.write(b'0'); f.flush(); f.seek(0)
        if sys.platform == 'win32':
            import msvcrt
            try: msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError: raise RuntimeError('Another receiver is running with this config')
            try: yield
            finally: f.seek(0); msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try: yield
            finally: fcntl.flock(f, fcntl.LOCK_UN)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--setup', action='store_true')
    mode.add_argument('--list', action='store_true')
    mode.add_argument('--chat', action='store_true', help='Interactive terminal chat (default)')
    mode.add_argument('--send', type=Path, help='Send a UTF-8 TXT file as one message')
    parser.add_argument('--config', type=Path, default=DEFAULT_CONFIG)
    parser.add_argument('--output', type=Path, help='Override answer directory this run')
    args = parser.parse_args()
    if hasattr(sys.stdout, 'reconfigure'): sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    try:
        require_windows()
        path = args.config.expanduser().resolve()
        with single_instance(path.with_suffix('.lock')):
            if args.setup: setup(path); return 0
            if args.list:
                for d in paired_devices(): print(f"{d['name']!r} [{d['address']}]")
                return 0
            if not path.exists(): raise ValueError('Run py -3 receiver.py --setup first')
            config = json.loads(path.read_text(encoding='utf-8'))
            parse_address(config['address'])
            if args.output: config['output'] = str(args.output.expanduser().resolve())
            Path(config['output']).mkdir(parents=True, exist_ok=True)
            state_path = path.with_name(path.stem + '-chat.json')
            state = json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else {'history': [], 'pending': None}
            print('BlueDrop AI | PC internet is not used | Ctrl+C stops\n/new = fresh conversation   /quit = exit\nReplies: ' + config['output'])
            if state.get('pending'):
                print('[Resuming saved request]')
                try: execute_pending(config, state, state_path)
                except RemoteError as exc: print('[Failed: ' + printable(str(exc)) + ']')
            if args.send:
                if args.send.stat().st_size > MAX_CONTEXT + 3: raise ValueError('TXT file exceeds 64 KiB')
                prompt = args.send.read_text(encoding='utf-8-sig')
                state['pending'] = new_request([], prompt)
                atomic_json(state_path, state)
                execute_pending(config, state, state_path)
                return 0
            while True:
                prompt = input('\nYou: ')
                if prompt.strip() == '/quit': return 0
                if prompt.strip() == '/new':
                    state['history'] = []; atomic_json(state_path, state); print('[New conversation]'); continue
                if not prompt.strip(): continue
                try:
                    state['pending'] = new_request(state['history'], prompt)
                    atomic_json(state_path, state)
                    execute_pending(config, state, state_path)
                except (RemoteError, ValueError) as exc: print('[Failed: ' + printable(str(exc)) + ']')
    except (KeyboardInterrupt, EOFError):
        print('\nStopped. Any pending request will resume on next launch.'); return 0
    except Exception as exc:
        print('Error: ' + printable(str(exc)), file=sys.stderr); return 1

if __name__ == '__main__':
    raise SystemExit(main())
