"""Launch the independent Tkinter Windows manager. No legacy GUI/service imports."""
import argparse
import multiprocessing
from pathlib import Path
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--demo', action='store_true', help='Disposable synthetic data; no live API/service calls')
    args = parser.parse_args(argv)
    import tkinter as tk
    from desktop_manager.app import ManagerWindow
    from desktop_manager.controller import Controller, Settings
    temporary = None
    if args.demo:
        from desktop_manager.demo import create_demo
        temporary, controller, values = create_demo()
        settings = None
    else:
        controller, settings, values = Controller(), Settings(), {}
    root = tk.Tk()
    ManagerWindow(root, controller, settings, values, demo=args.demo)
    try:
        root.mainloop()
    finally:
        controller.close()
        if temporary:
            temporary.cleanup()


if __name__ == '__main__':
    multiprocessing.freeze_support()
    main()
