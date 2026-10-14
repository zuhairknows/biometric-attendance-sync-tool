"""One HTTP delivery path for initial sends and retries; no credentials enter the ledger."""
import datetime as dt
from email.utils import parsedate_to_datetime
import json
import random
import time

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
    if http_status in (408, 429) or 500 <= http_status <= 599 or not http_status:
        return ('retry', http_status, 'transient', 'Transient destination or transport failure', None)
    if http_status == 417 and DUPLICATE_MESSAGE in text:
        return ('duplicate', http_status, 'already_exists', 'Confirmed existing Employee Checkin', None)
    if http_status in (400, 417) and any(message in text for message in MISSING_MESSAGES):
        return ('unmatched_employee', http_status, 'employee_mapping', 'Employee mapping was not found', None)
    if http_status in (400, 417) and INACTIVE_MESSAGE in text:
        return ('rejected', http_status, 'inactive_employee', 'Employee is inactive', None)
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


class FrappeClient:
    def __init__(self, config, request):
        app = 'hrms' if getattr(config, 'ERPNEXT_VERSION', 14) > 13 else 'erpnext'
        self.url = config.ERPNEXT_URL.rstrip('/') + '/api/method/' + app + '.hr.doctype.employee_checkin.employee_checkin.add_log_based_on_employee_field'
        self.headers = {'Authorization': 'token ' + config.ERPNEXT_API_KEY + ':' + config.ERPNEXT_API_SECRET,
                        'Accept': 'application/json'}
        self.timeout = getattr(config, 'ERPNEXT_REQUEST_TIMEOUT', getattr(config, 'REQUEST_TIMEOUT', 30))
        self.request = request

    def send(self, payload, now):
        try:
            response = self.request('POST', self.url, headers=self.headers, json=payload, timeout=self.timeout)
        except Exception:
            # Exception strings may contain URLs, authorization headers, or secrets.
            return ('retry', 0, 'transport', 'Destination transport failure or timeout', None), 0
        return classify(response.status_code, response_data(response)), retry_after(response, now)


def deliver_batch(ledger, client, batch_size=100, rate=2, base=30, maximum=3600,
                  auth_cooldown=900, lease_seconds=120, clock=time.time,
                  sleep=time.sleep, stop_requested=lambda: False, logger=None):
    """Claim only one row at a time; an entire backlog is never loaded into memory."""
    processed = 0
    last_request = None
    for _ in range(batch_size):
        if stop_requested():
            break
        if last_request is not None:
            sleep(max(0, 1 / rate - (clock() - last_request)))
        row = ledger.claim(clock(), lease_seconds)
        if row is None:
            break
        last_request = clock()
        outcome, guidance = client.send(json.loads(row['payload']), last_request)
        now = clock()
        delay = max(backoff(row['attempt_count'], base, maximum), guidance)
        cooldown = 0
        if outcome[0] == 'retry':
            # Destination-wide failure stops the batch after one request, preserving all work.
            if outcome[2] in ('authentication', 'unknown_response'):
                delay = max(delay, auth_cooldown)
            cooldown = now + delay
        ledger.finish(row, outcome, now, now + delay if outcome[0] == 'retry' else 0, cooldown)
        processed += 1
        if logger:
            logger.info('Durable delivery: punch=%s state=%s http=%s category=%s',
                        row['id'], outcome[0], outcome[1], outcome[2])
        if cooldown:
            break
    return processed
