"""One HTTP delivery path for initial sends and retries; no credentials enter the ledger."""
import datetime as dt
from email.utils import parsedate_to_datetime
import json
import random
import time
from urllib.parse import quote

DUPLICATE_MESSAGE = 'This employee already has a log with the same timestamp'
MISSING_MESSAGES = ('No Employee found for the given employee field value',
                    'No Employee found for attendance_device_id')
INACTIVE_MESSAGE = 'Transactions cannot be created for an Inactive Employee'


def response_data(response):
    try:
        data = json.loads(response.content if hasattr(response, 'content') else response._content)
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError, AttributeError):
        return {}


def classify(http_status, data):
    """Return (state, status, category, safe message, confirmed document name)."""
    # Only use server messages to recognize known errors; never retain tracebacks/HTML/headers.
    text = json.dumps({key: data.get(key) for key in ('exception', 'exc', '_server_messages', 'message')})
    if http_status == 200:
        message = data.get('message')
        name = message.get('name') if isinstance(message, dict) else None
        if isinstance(name, str) and name.strip() and len(name) <= 140:
            return ('synced', 200, None, None, name)
        return ('retry', 200, 'unconfirmed_response', 'Success response has no Checkin document name', None)
    if http_status in (401, 403):
        return ('retry', http_status, 'authentication', 'Destination credentials or permissions require correction', None)
    if http_status == 429:
        return ('retry', http_status, 'rate_limited', 'Destination rate limit; retry after cooldown', None)
    if http_status in (408, 429) or 500 <= http_status <= 599 or not http_status:
        return ('retry', http_status, 'transient', 'Transient destination or transport failure', None)
    if http_status in (400, 417) and any(message in text for message in MISSING_MESSAGES):
        return ('unmatched_employee', http_status, 'employee_mapping', 'Employee mapping was not found', None)
    if http_status in (400, 417) and INACTIVE_MESSAGE in text:
        return ('rejected', http_status, 'inactive_employee', 'Employee is inactive', None)
    if http_status == 417 and DUPLICATE_MESSAGE in text:
        # Employee errors can echo a supplied ID. Those errors take precedence above so
        # an echoed duplicate phrase in a missing employee ID cannot become success.
        error_type = data.get('exc_type')
        if error_type is None or error_type in ('ValidationError', 'DuplicateEntryError'):
            return ('duplicate', http_status, 'already_exists', 'Confirmed existing Employee Checkin', None)
    if http_status in (400, 417, 422):
        return ('rejected', http_status, 'business_validation', 'Destination rejected punch validation; inspect source and server logs', None)
    # An unfamiliar 404/redirect/etc. may mean broken routing, not a permanently bad punch.
    return ('retry', http_status, 'unknown_response', 'Unfamiliar destination response; operator investigation required', None)


def retry_after(response, now):
    value = getattr(response, 'headers', {}).get('Retry-After')
    if value is None:
        return 0
    try:
        seconds = int(value)
        return max(0, seconds)
    except (ValueError, TypeError):
        try:
            deadline = parsedate_to_datetime(value)
            if deadline.tzinfo is None:
                deadline = deadline.replace(tzinfo=dt.timezone.utc)
            return max(0, deadline.timestamp() - now)
        except (ValueError, TypeError, OverflowError):
            return 0


def backoff(attempt, base=30, maximum=3600, jitter=random.random):
    # Cap the exponent before exponentiation so years of retries cannot exhaust resources.
    delay = min(maximum, base * 2 ** min(max(0, attempt - 1), 20))
    return min(maximum, delay * (1 + 0.2 * jitter()))


def wait_for_slot(last_request, rate, clock, sleep, stop_requested):
    """Shared interruptible pacing for the normal writer and its recovery lane."""
    remaining = max(0, 1 / rate - (clock() - last_request)) if last_request is not None else 0
    while remaining > 0:
        if stop_requested():
            return False
        chunk = min(remaining, 0.5)
        sleep(chunk)
        remaining -= chunk
    return not stop_requested()


class FrappeClient:
    def __init__(self, config, request):
        app = 'hrms' if getattr(config, 'ERPNEXT_VERSION', 14) > 13 else 'erpnext'
        self.url = config.ERPNEXT_URL.rstrip('/') + '/api/method/' + app + '.hr.doctype.employee_checkin.employee_checkin.add_log_based_on_employee_field'
        self.headers = {'Authorization': 'token ' + config.ERPNEXT_API_KEY + ':' + config.ERPNEXT_API_SECRET,
                        'Accept': 'application/json'}
        self.timeout = getattr(config, 'ERPNEXT_REQUEST_TIMEOUT', getattr(config, 'REQUEST_TIMEOUT', 30))
        self.request = request
        self.confirmation_verified = (getattr(config, 'DURABLE_CONFIRM_READS_VERIFIED', False) is True
            and isinstance(getattr(config, 'DURABLE_SERVER_REVIEW_REFERENCE', None), str)
            and bool(config.DURABLE_SERVER_REVIEW_REFERENCE.strip()))
        self.resource_url = config.ERPNEXT_URL.rstrip('/') + '/api/resource/'

    def confirm(self, payload, before_request=None):
        """Positive identity proof only. Empty/filtered/failed reads never permit another POST."""
        if not self.confirmation_verified:
            return None
        try:
            stamp = dt.datetime.fromisoformat(payload['timestamp'])
            if stamp.tzinfo is not None:
                return None
            expected = stamp.replace(microsecond=0)
            log_type = payload['log_type'] or ''
            def read(doctype, fields, filters):
                if before_request is not None and not before_request():
                    return None
                response = self.request('GET', self.resource_url + quote(doctype, safe=''), headers=self.headers,
                    params=dict(fields=json.dumps(fields), filters=json.dumps(filters), limit_page_length=2),
                    timeout=self.timeout, allow_redirects=False)
                if response.status_code != 200:
                    return None
                data = response_data(response).get('data')
                return data if isinstance(data, list) else None
            employees = read('Employee', ['name', 'status', 'attendance_device_id'],
                             [['attendance_device_id', '=', payload['employee_field_value']]])
            if (not employees or len(employees) != 1 or employees[0].get('status') != 'Active'
                    or employees[0].get('attendance_device_id') != payload['employee_field_value']):
                return None
            employee = employees[0]['name']
            rows = read('Employee Checkin', ['name', 'employee', 'time', 'log_type'],
                [['employee', '=', employee], ['time', '=', str(expected)], ['log_type', '=', log_type]])
            if not rows or len(rows) != 1:
                return None
            row = rows[0]
            if (row.get('employee') == employee and row.get('log_type') == log_type
                    and dt.datetime.fromisoformat(row['time']) == expected
                    and isinstance(row.get('name'), str) and 0 < len(row['name']) <= 140):
                return row['name']
        except Exception:
            pass  # Remote messages, names, URLs and credentials never enter failure diagnostics.
        return None

    def send(self, payload, now):
        try:
            response = self.request('POST', self.url, headers=self.headers, json=payload, timeout=self.timeout,
                                    allow_redirects=False)
        except Exception:
            # Exception strings may contain URLs, authorization headers, or secrets.
            return ('retry', 0, 'transport', 'Destination transport failure or timeout', None), 0
        return classify(response.status_code, response_data(response)), retry_after(response, now)


def deliver_batch(ledger, client, batch_size=100, rate=2, base=30, maximum=3600,
                  auth_cooldown=900, lease_seconds=120, clock=time.time,
                  sleep=time.sleep, stop_requested=lambda: False, logger=None, not_before=None,
                  recovery_guard=None):
    """Claim only one row at a time; an entire backlog is never loaded into memory."""
    processed = 0
    last_request = getattr(recovery_guard, 'last_request_time', None)
    def pace_confirmation_read():
        nonlocal last_request
        if not wait_for_slot(last_request, rate, clock, sleep, stop_requested):
            return False
        last_request = clock()
        return True
    for _ in range(batch_size):
        if stop_requested():
            break
        if last_request is not None:
            remaining = max(0, 1 / rate - (clock() - last_request))
            while remaining > 0:
                if stop_requested():
                    return processed
                chunk = min(remaining, 0.5)
                sleep(chunk)
                remaining -= chunk
        if stop_requested():
            break
        row = (ledger.claim(clock(), lease_seconds, allow_recovery_overlap=True) if recovery_guard
               else ledger.claim(clock(), lease_seconds))
        if row is None:
            break
        last_request = clock()
        payload = json.loads(row['payload'])
        if not_before is not None and dt.datetime.fromisoformat(payload['timestamp']) < not_before:
            ledger.finish(row, ('rejected', 0, 'cutover_hold', 'Historical punch retained outside approved live cutover scope', None), clock())
            if logger:
                logger.warning(json.dumps(dict(event='durable_cutover_hold', punch=row['id'])))
            processed += 1
            continue
        # A crash, timeout or 5xx can hide a remote commit. Never blindly POST it again.
        needs_confirmation = row['attempt_count'] > 1 and row['error_category'] not in ('authentication', 'rate_limited', 'peer_uncertain')
        guarded = None
        if recovery_guard and ledger.recovery_overlap(row):
            guarded = recovery_guard(row, pace_confirmation_read)
            # Guard reads participate in the same pacing as the following normal POST.
            if guarded is None and not pace_confirmation_read():
                guarded = ('retry', 0, 'confirmation_pending', 'Recovery overlap verification interrupted', None)
        if guarded is not None:
            outcome, guidance = guarded, 0
        elif not needs_confirmation and ledger.has_uncertain_peer(row):
            outcome = ('retry', 0, 'peer_uncertain', 'Related source identity is uncertain; no POST attempted', None)
            guidance = 0
        elif needs_confirmation:
            name = client.confirm(payload, before_request=pace_confirmation_read)
            outcome = (('duplicate', 200, 'destination_confirmed', 'Existing destination identity verified', name)
                       if name else ('retry', 0, 'confirmation_pending', 'Uncertain submission requires destination evidence; no POST retried', None))
            guidance = 0
        else:
            outcome, guidance = client.send(payload, last_request)
            if outcome[0] == 'duplicate':
                name = client.confirm(payload, before_request=pace_confirmation_read)
                outcome = (('duplicate', outcome[1], 'destination_confirmed', 'Existing destination identity verified', name)
                           if name else ('retry', outcome[1], 'confirmation_pending', 'Duplicate response requires destination evidence', None))
        now = clock()
        delay = max(backoff(row['attempt_count'], base, maximum), guidance)
        cooldown = 0
        if outcome[0] == 'retry':
            # Local uncertainty holds this row; it must not suspend unrelated fresh work.
            if outcome[2] not in ('confirmation_pending', 'peer_uncertain'):
                if outcome[2] in ('authentication', 'unknown_response'):
                    delay = max(delay, auth_cooldown)
                cooldown = now + delay
        ledger.finish(row, outcome, now, now + delay if outcome[0] == 'retry' else 0, cooldown)
        processed += 1
        if logger:
            logger.info(json.dumps(dict(event='durable_delivery', punch=row['id'], state=outcome[0],
                                        http=outcome[1], category=outcome[2])))
        if cooldown:
            break
    return processed
