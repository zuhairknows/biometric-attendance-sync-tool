"""Verify ledger evidence by reusing the original Phase 1 normalizer."""
import json

from punch_normalization import normalize
from scripts.attendance_reconciliation import local_time


def verified_source(row):
    """Rebuild the original identity without a cutover filter or state transition."""
    evidence, payload = json.loads(row['source_evidence']), json.loads(row['payload'])
    raw = dict(evidence)
    raw['uid'] = None if raw['uid'] in ('', 'None') else raw['uid']
    rebuilt = normalize(raw, dict(device_id=row['device_id'], punch_direction=payload['log_type'],
                                 latitude=payload['latitude'], longitude=payload['longitude']))
    fields = ('identity_key', 'device_id', 'user_id', 'device_uid', 'punch_timestamp', 'punch_code', 'status_code')
    if (rebuilt['state'] == 'invalid_record' or any(rebuilt[f] != row[f] for f in fields)
            or json.loads(rebuilt['payload']) != payload):
        raise ValueError
    return local_time(row['punch_timestamp']), payload['log_type'] or ''
