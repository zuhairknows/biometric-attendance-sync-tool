"""Manual loopback-only WSGI host. No reloader, service installation or firewall action."""
import logging
import webbrowser

from waitress import create_server
from .web import ORIGIN


def serve(app, *, open_browser=True):
    server = None
    logging.getLogger('waitress').disabled = True
    try:
        # Fixed binding: profile values and environment cannot expose a LAN listener.
        server = create_server(app, host='127.0.0.1', port=8765, threads=2,
            connection_limit=16, max_request_body_size=32768, max_request_header_size=16384,
            channel_timeout=15, cleanup_interval=5)
        print('Biometric Manager: ' + ORIGIN + ' (Ctrl+C stops only this manager)')
        if open_browser:
            try:
                webbrowser.open(ORIGIN)
            except Exception:
                print('Browser could not open. Open the local address above manually.')
        server.run()
    finally:
        app.extensions['manager'].close()
        if server:
            server.close()
            server.task_dispatcher.shutdown(timeout=3)
