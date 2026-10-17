"""Authenticated GET-only Frappe v1 resource client with bounded pagination."""
import json
from urllib.parse import quote, urlsplit


class VerificationError(Exception):
    """Only fixed diagnostic codes may leave this boundary."""


class ReadOnlyFrappeClient:
    FIELDS = {
        'Employee': ['name', 'attendance_device_id', 'status', 'modified'],
        'Employee Checkin': ['name', 'employee', 'time', 'log_type', 'device_id', 'modified'],
    }

    def __init__(self, url, key, secret, request, *, page_size=200, max_pages=10000,
                 timeout=30, cancelled=lambda: False):
        parsed = urlsplit(url)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment or parsed.path not in ('', '/')
                or not key or not secret or any(c in key + secret for c in '\r\n')
                or type(page_size) is not int or not 1 <= page_size <= 1000
                or type(max_pages) is not int or max_pages < 1 or not 0 < timeout <= 120):
            raise VerificationError('invalid_client_settings')
        self.url = url.rstrip('/')
        self._headers = {'Authorization': 'token ' + key + ':' + secret, 'Accept': 'application/json'}
        self._request = request
        self.page_size, self.max_pages, self.timeout = page_size, max_pages, timeout
        self.cancelled = cancelled

    def scan(self, doctype, filters):
        fields = self.FIELDS[doctype]  # No caller-supplied method or submission URL.
        expected = self.count(doctype, filters)
        rows, previous = [], None
        for _ in range(self.max_pages):
            if self.cancelled():
                raise KeyboardInterrupt
            try:
                response = self._request('GET', self.url + '/api/resource/' + quote(doctype, safe=''),
                    headers=self._headers, params=dict(fields=json.dumps(fields), filters=json.dumps(filters),
                    order_by='name asc', limit_start=len(rows), limit_page_length=self.page_size),
                    timeout=self.timeout, allow_redirects=False)
                if response.status_code != 200:
                    raise ValueError
                body = response.json()
                page = body['data']
                if not isinstance(page, list) or len(page) > self.page_size:
                    raise ValueError
                for row in page:
                    if (not isinstance(row, dict) or any(f not in row for f in fields)
                            or not isinstance(row['name'], str) or not row['name']
                            or (previous is not None and row['name'] <= previous)
                            or not isinstance(row['modified'], str) or not row['modified']):
                        raise ValueError
                    previous = row['name']
            except Exception:
                # Transport/server errors can echo credentials or employee data.
                raise VerificationError('api_or_pagination_unverified') from None
            if not page:
                if len(rows) != expected or self.count(doctype, filters) != expected:
                    raise VerificationError('pagination_count_mismatch')
                return rows
            rows.extend(page)
            # Even a short page is followed by an explicit empty terminal page.
            # This also handles servers that cap page sizes below our request.
        raise VerificationError('pagination_limit_exceeded')

    def count(self, doctype, filters):
        """An independent count detects silently truncated/empty resource pages."""
        if doctype not in self.FIELDS:
            raise VerificationError('unsupported_doctype')
        if self.cancelled():
            raise KeyboardInterrupt
        try:
            response = self._request('GET', self.url + '/api/method/frappe.client.get_count',
                headers=self._headers, params=dict(doctype=doctype, filters=json.dumps(filters)),
                timeout=self.timeout, allow_redirects=False)
            count = response.json()['message']
            if response.status_code != 200 or type(count) is not int or count < 0:
                raise ValueError
            return count
        except Exception:
            raise VerificationError('destination_count_unverified') from None

    def snapshot(self, start, end):
        filters = [['time', '>=', str(start)], ['time', '<=', str(end)]]
        employees = self.scan('Employee', [])  # Include inactive and duplicate mappings.
        checkins = self.scan('Employee Checkin', filters)  # All employees/devices.
        # Offset pagination alone cannot prove a stable view. Detect observable drift
        # with a second full pass; server-side visibility/quiescence still need review.
        if employees != self.scan('Employee', []) or checkins != self.scan('Employee Checkin', filters):
            raise VerificationError('destination_changed_during_investigation')
        return employees, checkins
