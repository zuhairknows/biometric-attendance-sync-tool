"""Narrow MariaDB SELECT adapter. No Frappe lifecycle, HTTP, device, or write APIs."""
import datetime as dt
import json
import re

from scripts.attendance_reconciliation import require
from scripts.frappe_export_review import fingerprint

TABLES = ('tabEmployee', 'tabEmployee Checkin', 'tabSingles', 'tabServer Script', 'tabDefaultValue')
SQL = {
    'grants': 'SHOW GRANTS FOR CURRENT_USER()',
    'tls': "SHOW SESSION STATUS LIKE 'Ssl_cipher'",
    'identity': 'SELECT DATABASE() AS database_name, @@hostname AS server_hostname, @@server_id AS server_id',
    'isolation': 'SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ',
    'readonly': 'SET SESSION TRANSACTION READ ONLY',
    'begin': 'START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY',
    'state': 'SELECT @@tx_isolation AS isolation_level, @@tx_read_only AS read_only, @@in_transaction AS in_transaction',
    'clock': 'SELECT UTC_TIMESTAMP(6) AS snapshot_time',
    'engines': 'SELECT TABLE_NAME AS table_name, ENGINE AS engine, TABLE_TYPE AS table_type FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s AND TABLE_NAME IN (%s,%s,%s,%s,%s)',
    'columns': "SELECT TABLE_NAME AS table_name, COLUMN_NAME AS column_name, DATA_TYPE AS data_type, DATETIME_PRECISION AS datetime_precision, COLLATION_NAME AS collation_name FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=%s AND ((TABLE_NAME='tabEmployee' AND COLUMN_NAME='attendance_device_id') OR (TABLE_NAME='tabEmployee Checkin' AND COLUMN_NAME='time'))",
    'timezone': "SELECT value FROM tabSingles WHERE doctype='System Settings' AND field='time_zone'",
    'apps': "SELECT defvalue FROM tabDefaultValue WHERE defkey='installed_apps' AND parent='__global'",
    'scripts': "SELECT name, script_type, reference_doctype, doctype_event, api_method, SHA2(COALESCE(script,''),256) AS script_sha256 FROM `tabServer Script` WHERE disabled=0 AND ((script_type='DocType Event' AND reference_doctype='Employee Checkin') OR script_type='API') ORDER BY name",
    'mapping_count': 'SELECT COUNT(*) AS row_count FROM tabEmployee WHERE attendance_device_id=%s',
    'mapping': 'SELECT name AS employee, attendance_device_id, status FROM tabEmployee WHERE attendance_device_id=%s ORDER BY name',
    'checkin_count': 'SELECT COUNT(*) AS row_count FROM `tabEmployee Checkin` WHERE time>=%s AND time<=%s',
    'checkins_first': 'SELECT name, employee, time, device_id, log_type FROM `tabEmployee Checkin` WHERE time>=%s AND time<=%s ORDER BY name LIMIT %s',
    'checkins_next': 'SELECT name, employee, time, device_id, log_type FROM `tabEmployee Checkin` WHERE time>=%s AND time<=%s AND name>%s ORDER BY name LIMIT %s',
}


def validate_grants(rows, database):
    """Only direct SELECT/USAGE grants on this site. Roles, columns, FILE/EXECUTE fail."""
    covered = set()
    require(rows, 'database_grants_unavailable')
    for row in rows:
        require(len(row) == 1 and isinstance(next(iter(row.values())), str), 'database_grants_unsupported')
        grant = next(iter(row.values()))
        require('WITH GRANT OPTION' not in grant.upper(), 'database_account_not_select_only')
        match = re.match(r'^GRANT (USAGE|SELECT) ON (\*\.\*|`[^`]+`\.\*|`[^`]+`\.`[^`]+`) TO ', grant, re.I)
        require(match is not None, 'database_account_not_select_only')
        privilege, scope = match.groups()
        if privilege.upper() == 'USAGE':
            require(scope == '*.*', 'database_grants_unsupported')
        else:
            require(scope.startswith('`' + database + '`.'), 'database_grants_not_site_scoped')
            if scope == '`' + database + '`.*':
                covered.update(TABLES)
            else:
                table = scope.split('`.`', 1)[1][:-1]
                require(table in TABLES, 'database_grants_not_minimal')
                covered.add(table)
    require(covered == set(TABLES), 'database_select_permissions_incomplete')


def connect(profile, password):
    """Lazy optional driver; no default files, init SQL, local infile or plaintext TLS."""
    from importlib.metadata import version
    # The pinned release rejects non-TLS servers before authentication. Older drivers
    # can differ; do not rely on a post-authentication cipher check to protect passwords.
    require(version('PyMySQL') == '1.2.3', 'database_driver_version_not_reviewed')
    import pymysql
    return pymysql.connect(host=profile['host'], port=profile['port'], database=profile['database'],
        user=profile['user'], password=password, charset='utf8mb4', autocommit=True,
        connect_timeout=10, read_timeout=60, write_timeout=10, local_infile=False,
        ssl_ca=profile['tls_ca'], ssl_verify_cert=True, ssl_verify_identity=True,
        cursorclass=pymysql.cursors.DictCursor)


class Snapshot:
    def __init__(self, connection, database, review, semantics, hooks, page_size=1000):
        self.connection, self.database = connection, database
        self.review, self.semantics, self.hooks = review, semantics, hooks
        self.page_size = page_size

    def query(self, key, parameters=()):
        # Callers choose a fixed statement key, never a SQL string or table name.
        require(key in SQL, 'non_readonly_query_rejected')
        with self.connection.cursor() as cursor:
            cursor.execute(SQL[key], parameters)
            return cursor.fetchall()

    def one(self, key, parameters=()):
        rows = self.query(key, parameters)
        require(len(rows) == 1, 'database_metadata_not_unique')
        return rows[0]

    def state(self):
        state = self.one('state')
        require(state['isolation_level'].upper().replace('_', '-') == 'REPEATABLE-READ'
                and state['read_only'] == 1 and state['in_transaction'] == 1, 'snapshot_not_readonly_repeatable_read')

    def begin(self):
        validate_grants(self.query('grants'), self.database)
        require(bool(self.one('tls')['Value']), 'database_tls_not_active')
        identity = self.one('identity')
        require(identity['database_name'] == self.database and fingerprint(identity) == self.review['database_identity_sha256'], 'authoritative_database_mismatch')
        self.query('isolation')
        self.query('readonly')
        before = self.one('clock')['snapshot_time']
        self.query('begin')
        self.state()
        after = self.one('clock')['snapshot_time']
        require(isinstance(before, dt.datetime) and isinstance(after, dt.datetime) and before.tzinfo is None
                and after.tzinfo is None and before <= after, 'database_clock_invalid')
        # Bound the transaction start instead of pretending a later metadata query is its time.
        self.timestamp = before.replace(tzinfo=dt.timezone.utc)
        self.started_not_after = after.replace(tzinfo=dt.timezone.utc)
        engines = self.query('engines', (self.database, *TABLES))
        require(len(engines) == len(TABLES) and {r['table_name'] for r in engines} == set(TABLES)
                and all(r['engine'] == 'InnoDB' and r['table_type'] == 'BASE TABLE' for r in engines), 'nontransactional_or_filtered_source_unsupported')
        columns = self.query('columns', (self.database,))
        require(len(columns) == 2, 'database_columns_unverified')
        employee = [r for r in columns if r['table_name'] == 'tabEmployee' and r['column_name'] == 'attendance_device_id']
        checkin = [r for r in columns if r['table_name'] == 'tabEmployee Checkin' and r['column_name'] == 'time']
        require(len(employee) == len(checkin) == 1 and employee[0]['data_type'] == 'varchar'
                and bool(employee[0]['collation_name']) and checkin[0]['data_type'] == 'datetime', 'database_column_semantics_unsupported')
        require(checkin[0]['datetime_precision'] in (0, 6) and (self.semantics['timestamp_precision'] != 'microseconds'
                or checkin[0]['datetime_precision'] == 6), 'timestamp_storage_precision_mismatch')
        timezone = self.one('timezone')['value']
        require(timezone == self.review['site_timezone'], 'deployed_site_timezone_mismatch')
        apps = json.loads(self.one('apps')['defvalue'])
        require(sorted(apps) == sorted(self.hooks['installed_apps']), 'installed_apps_inventory_changed')
        scripts = self.query('scripts')
        # Bodies are hashed by the database: credentials in unrelated API scripts never leave it.
        require(fingerprint(scripts) == self.review['server_scripts_sha256'], 'server_scripts_inventory_changed')
        endpoint = self.review['endpoint_path']
        require(not any(r['script_type'] == 'DocType Event' or r['api_method'] in (endpoint, 'add_log_based_on_employee_field')
                        for r in scripts), 'employee_checkin_server_scripts_unsupported')
        self.column_evidence = fingerprint(columns)
        return self

    def mappings(self, users):
        result, total_matches = [], 0
        for user in users:
            count = self.one('mapping_count', (user,))['row_count']
            require(type(count) is int and 0 <= count <= 100000, 'employee_lookup_count_unbounded')
            matches = self.query('mapping', (user,))
            require(type(count) is int and count == len(matches), 'employee_lookup_incomplete')
            require(len({r['employee'] for r in matches}) == len(matches) and all(
                all(isinstance(r.get(k), str) for k in ('employee', 'attendance_device_id', 'status'))
                and r['employee'] for r in matches), 'employee_lookup_fields_invalid')
            result.append(dict(user_id=user, matches=matches))
            total_matches += count
        self.state()
        return result, total_matches

    def checkins(self, start, end):
        before = self.one('checkin_count', (start, end))['row_count']
        require(type(before) is int and 0 <= before <= 500000, 'checkin_count_unbounded')
        rows, names, last = [], set(), None
        pages = 0
        while True:
            page = self.query('checkins_first', (start, end, self.page_size)) if last is None else self.query(
                'checkins_next', (start, end, last, self.page_size))
            require(len(page) <= self.page_size, 'checkin_page_size_invalid')
            if not page:
                break
            pages += 1
            for row in page:
                require(isinstance(row.get('name'), str) and row['name'] and row['name'] not in names
                        and isinstance(row.get('employee'), str) and row['employee'], 'checkin_page_duplicate_or_invalid')
                names.add(row['name'])
                stamp = row.get('time')
                require(isinstance(stamp, dt.datetime) and stamp.tzinfo is None and start <= stamp <= end, 'checkin_timestamp_invalid')
                # Preserve fractional precision even if historical data differs from today's validator.
                rows.append(dict(name=row['name'], employee=row['employee'], time=stamp.isoformat(' ', timespec='microseconds'),
                                 device_id=row['device_id'] if row['device_id'] is not None else '',
                                 log_type=row['log_type'] if row['log_type'] is not None else ''))
                require(isinstance(rows[-1]['device_id'], str) and rows[-1]['log_type'] in ('', 'IN', 'OUT'), 'checkin_fields_unsupported')
            last = page[-1]['name']
            require(len(rows) <= before, 'checkin_count_exceeded')
        after = self.one('checkin_count', (start, end))['row_count']
        require(before == len(rows) == after, 'checkin_export_incomplete')
        self.state()
        self.completeness = dict(independent_count_before=before, independent_count_after=after,
                                 exported_rows=len(rows), keyset_pages=pages, page_size=self.page_size)
        return rows

    def close(self):
        # Roll back even a read-only transaction; never commit or reuse this connection.
        try:
            self.connection.rollback()
        finally:
            self.connection.close()
