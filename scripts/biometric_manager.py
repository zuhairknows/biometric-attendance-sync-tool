"""Launch the independent loopback Flask manager; no service/config imports."""
import argparse
import multiprocessing
import os
from pathlib import Path
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--demo', action='store_true', help='Disposable synthetic data; no live API/service calls')
    mode.add_argument('--profile', help='Absolute protected startup profile; browser cannot add file paths')
    parser.add_argument('--monitor-only', action='store_true', help='Allow Dashboard and Devices only; block investigation, recovery and exports')
    parser.add_argument('--no-browser', action='store_true', help='Print the local address without opening a browser')
    args = parser.parse_args(argv)
    temporary, controller = None, None
    try:
        from manager_app.controller import Controller
        from manager_app.profile import load_profile
        from manager_app.web import create_app
        from manager_app.runtime import serve
        if args.demo:
            from manager_app.demo import create_demo
            temporary, controller, values = create_demo(monitor_only=args.monitor_only)
        else:
            path = args.profile or str(Path(os.environ.get('LOCALAPPDATA', '')) / 'MYFPF' / 'BiometricSyncManager' / 'web-profile.json')
            values = load_profile(path)
            controller = Controller()
        serve(create_app(controller, values, demo=args.demo, monitor_only=args.monitor_only), open_browser=not args.no_browser)
        return 0
    except KeyboardInterrupt:
        return 0
    except ImportError:
        print('Manager dependencies missing. Use the isolated environment and requirements-manager.txt.')
        return 1
    except Exception:
        print('Manager could not start. Check the private profile, dependencies, and whether local port 8765 is already in use.')
        return 1
    finally:
        if controller:
            controller.close()
        if temporary:
            temporary.cleanup()


if __name__ == '__main__':
    multiprocessing.freeze_support()
    raise SystemExit(main())
