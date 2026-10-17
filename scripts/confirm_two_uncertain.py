"""Run separately from production code; only GET transport exists. Default: preview."""
import argparse
import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import warnings
from types import SimpleNamespace
from urllib.parse import urlencode, quote


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-root', required=True)
    parser.add_argument('--config', required=True)
    parser.add_argument('--db', required=True)
    parser.add_argument('--apply-approved-two', action='store_true')
    args = parser.parse_args(argv)
    try:
        root = Path(args.runtime_root).resolve(strict=True)
        # Validate only the pinned modules we load, allowing Windows CRLF checkout.
        pins = {'sync_ledger': 'becff8ae23d6bb1acb12878c48ecbd9948d14d2b883c05a9b8031191d1865fd0',
                'durable_startup': 'c657f64d327ce0b9f89560cef94bf49c556b6a05d7059b7631cc4d41ee5456ce'}
        def load(name, path):
            spec = importlib.util.spec_from_file_location(name, path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
            return module
        for name, digest in pins.items():
            data = (root / (name + '.py')).read_bytes().replace(b'\r\n', b'\n')
            if hashlib.sha256(data).hexdigest() != digest:
                raise ValueError()
        for name in pins:
            load(name, root / (name + '.py'))
        tools = Path(__file__).resolve().parent
        transport = load('_approved_get', tools / 'verify_integration_get_only.py')
        engine = load('_approved_confirmation', tools / 'durable_confirmation.py')
        source = transport.read_bytes(args.config)
        authorization = transport.credentials(source)
        # The AST-only loader already rejects executable or indirect configuration.
        settings = {}
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            tree = ast.parse(source.decode('utf-8-sig'))
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if target.id in ('ERPNEXT_VERSION', 'DURABLE_CONFIRM_READS_VERIFIED'):
                        transport.require(target.id not in settings)
                        settings[target.id] = ast.literal_eval(node.value)
        transport.require(type(settings.get('ERPNEXT_VERSION')) is int
                          and settings['ERPNEXT_VERSION'] == 15
                          and settings.get('DURABLE_CONFIRM_READS_VERIFIED') is False)
        config = SimpleNamespace(ERPNEXT_URL='https://erp.myfpf.com', ERPNEXT_VERSION=15)
        engine.binding(args.db, config)
        sys.modules['durable_startup'].private_storage(Path(args.db))
        transport.require(transport.get_json('/api/method/frappe.auth.get_logged_user', authorization)
                          .get('message') == 'attendance.sync@myfpf.com')
        def get(doctype, fields, filters):
            path = '/api/resource/' + quote(doctype, safe='') + '?' + urlencode(dict(
                fields=json.dumps(fields), filters=json.dumps(filters), limit_page_length=2))
            return transport.get_json(path, authorization).get('data')
        print(json.dumps(engine.reconcile(args.db, config, get, args.apply_approved_two), sort_keys=True))
        return 0
    except Exception:
        # Exceptions can contain credentials, response data or private paths.
        print('CONFIRMATION_REFUSED: no submission attempted; inspect both record statuses before retry')
        return 1


if __name__ == '__main__':
    sys.dont_write_bytecode = True
    raise SystemExit(main())
