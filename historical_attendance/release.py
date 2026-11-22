"""Fail-closed release authorization, independent of per-record approvals.

An administrator must review and pin this private evidence before activation.
Assertions are operational attestations, never proof supplied by an API probe.
No template or manager action enables execution automatically.
"""
import ipaddress
from pathlib import Path
import time

from durable_startup import activation_path, private_storage
from scripts.attendance_reconciliation import verified_json
from .client import VerificationError
from .recovery import file_hash, require
from .https_transport import HTTPS

CLAIMS = ('deployed_handlers_reviewed', 'complete_destination_visibility',
          'employee_equality_reviewed', 'timezone_precision_reviewed',
          'duplicate_atomicity_or_scoped_quiescence', 'historical_payload_accepted',
          'service_token_and_inbox_acl_verified', 'single_writer_restart_verified',
          'backup_and_rollback_verified')
ROOT = Path(__file__).resolve().parents[1]


def code_files():
    # Pin every production Python module used by the service/historical lane.
    return sorted([p.name for p in ROOT.glob('*.py') if not p.name.startswith('local_config')]
                  + [str(p.relative_to(ROOT)).replace('\\', '/') for p in (ROOT / 'historical_attendance').glob('*.py')]
                  + [str(p.relative_to(ROOT)).replace('\\', '/') for p in (ROOT / 'scripts').glob('*.py')])


def validate(path, fingerprint, *, site, contract_hash, ledger, storage_check=private_storage, now=time.time):
    path = Path(path)
    require(path.is_absolute(), 'recovery_release_path_invalid')
    storage_check(path)
    value = verified_json(path, fingerprint)[0]
    require(value.get('schema') == 'attendance-service-release-v1' and value.get('enabled') is True,
            'recovery_release_disabled')
    require(value.get('site_url') == site and value.get('contract_sha256') == contract_hash
            and value.get('ledger') == str(Path(ledger).resolve())
            and value.get('activation_sha256') == file_hash(activation_path(Path(ledger))), 'recovery_release_binding_invalid')
    require(type(value.get('approved_at')) in (int, float) and type(value.get('expires_at')) in (int, float)
            and value['approved_at'] <= now() < value['expires_at'] <= value['approved_at'] + 86400,
            'recovery_release_expired')
    require(all(isinstance(value.get('evidence', {}).get(c), str) and 0 < len(value['evidence'][c].strip()) <= 200
                for c in CLAIMS), 'recovery_release_prerequisites_missing')
    require(isinstance(value.get('approval_reference'), str) and bool(value['approval_reference'].strip()),
            'recovery_release_approval_missing')
    require(set(value.get('code_sha256', {})) == set(code_files()) and all(
        file_hash(ROOT / name) == digest for name, digest in value['code_sha256'].items()), 'recovery_release_code_changed')
    try:
        ipaddress.ip_address(value['address'])
    except (KeyError, ValueError):
        raise VerificationError('recovery_release_address_invalid') from None
    return value


class ServiceTarget:
    """Constructed only by the existing service after explicit configuration."""
    def __init__(self, config, ledger, stopped, storage_check=private_storage):
        self.config, self.ledger, self.storage_check = config, ledger, storage_check
        self.url = config.ERPNEXT_URL.rstrip('/')
        release = self.validate()
        self.transport = HTTPS(self.url, release['address'], stopped=stopped)

    def validate(self):
        return validate(self.config.HISTORICAL_RECOVERY_RELEASE, self.config.HISTORICAL_RECOVERY_RELEASE_SHA256,
            site=self.url, contract_hash=self.config.HISTORICAL_RECOVERY_CONTRACT_SHA256,
            ledger=self.ledger, storage_check=lambda path: self.storage_check(path,
                service_sid=getattr(self.config, 'HISTORICAL_REQUEST_AUTHOR_SID', None)))

    def request(self, method, url, **options):
        self.validate()  # Revocation, expiry and source changes fail before transport.
        return self.transport.request(method, url, **options)
