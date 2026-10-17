"""One localhost HTTPS exchange for Step 4. No production modules or outbound sockets."""
import argparse
import json
from pathlib import Path
import socket
import ssl
import sys


def run(root, resume_attempt=None):
    root = Path(root).resolve(strict=True)
    if not root.name.startswith('FPF-Phase1-Staging-') or str(root).startswith('\\\\'):
        raise ValueError('invalid_stage')
    code, data = root / 'service-boundary/code', root / 'service-boundary/data'
    cert, key = root / 'localhost-cert.pem', root / 'localhost-key.pem'
    for path in (code, data, cert, key):
        if path.resolve(strict=True) != path:
            raise ValueError('reparse_path_refused')
    endpoint, result = code / 'mock-endpoint.json', data / 'mock-result.json'
    port = 0
    ready = endpoint
    if resume_attempt is not None:
        if len(resume_attempt) != 32 or any(c not in '0123456789abcdef' for c in resume_attempt):
            raise ValueError('invalid_resume_attempt')
        attempt = data / ('step4-resume-' + resume_attempt)
        if attempt.resolve(strict=True) != attempt or not attempt.is_dir():
            raise ValueError('invalid_attempt_directory')
        with endpoint.open(encoding='utf-8') as file:
            previous = json.load(file)
        if set(previous) != {'port'} or type(previous['port']) is not int or not 1 <= previous['port'] <= 65535:
            raise ValueError('invalid_retained_endpoint')
        port = previous['port']
        ready, result = attempt / 'mock-ready.json', attempt / 'mock-result.json'
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(cert, key)

    def guard(event, args):
        if event in ('socket.connect', 'socket.getaddrinfo', 'socket.gethostbyname', 'socket.gethostbyaddr',
                     'socket.sendto', 'socket.sendmsg', 'os.system') or event.startswith(('subprocess.', 'ctypes.')):
            raise PermissionError('mock_outbound_or_process_denied')
        if event == 'socket.__new__' and (args[1] != socket.AF_INET or args[2] != socket.SOCK_STREAM):
            raise PermissionError('mock_socket_denied')
        if event == 'socket.bind' and args[1] != ('127.0.0.1', port):
            raise PermissionError('mock_bind_denied')
        if event == 'open' and not isinstance(args[0], int) and args[1] and any(c in args[1] for c in 'wax+'):
            if Path(args[0]).resolve() not in (ready, result):
                raise PermissionError('mock_write_denied')

    sys.addaudithook(guard)
    passed = False
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(('127.0.0.1', port))
            listener.listen(1)
            listener.settimeout(45)
            # Resume rebinds the retained port and never rewrites the old endpoint.
            with ready.open('x', encoding='utf-8') as file:
                json.dump({'port': listener.getsockname()[1]}, file)
            peer, _ = listener.accept()
            with peer:
                peer.settimeout(8)
                with context.wrap_socket(peer, server_side=True) as stream:
                    request = b''
                    while len(request) < 1024 and not request.endswith(b'\r\n\r\n'):
                        value = stream.recv(1)
                        if not value:
                            break
                        request += value
                    if request != b'GET /health HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n':
                        raise ValueError('unexpected_synthetic_request')
                    stream.sendall(b'HTTP/1.1 200 OK\r\nContent-Length: 19\r\nConnection: close\r\n\r\nSTAGE-SYNTHETIC-OK\n')
                    passed = True
    finally:
        with result.open('x', encoding='utf-8') as file:
            json.dump({'status': 'PASS' if passed else 'FAIL', 'synthetic_exchanges': int(passed)}, file)
    return 0 if passed else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--stage-root', required=True)
    parser.add_argument('--resume-attempt')
    args = parser.parse_args()
    try:
        if not (sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode):
            raise ValueError('isolated_flags_required')
        sys.exit(run(args.stage_root, args.resume_attempt))
    except Exception:
        print('LOCAL MOCK FAIL: details suppressed')
        sys.exit(1)
