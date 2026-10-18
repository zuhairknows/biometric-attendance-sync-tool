"""One manually initiated management action at a time; no scheduler or polling worker."""
import secrets
import threading

from .controller import INVESTIGATION_FAILURES, ManagerError


class State:
    def __init__(self, controller, values, devices):
        self.controller, self.values, self.devices = controller, dict(values), devices
        self.dashboard = None
        self.investigation = None
        self.batches = []
        self.lock = threading.Lock()
        self.thread = None
        self.job = None
        self.closed = False

    def start(self, label, task, accept, destination):
        # Serializing every management action also protects the inherited CLI env seam.
        if self.closed or not self.lock.acquire(blocking=False):
            raise ManagerError('another_management_action_is_running')
        job = dict(id=secrets.token_hex(16), label=label, status='running', destination=destination, error='')
        self.job = job

        def work():
            try:
                result = task()
                if self.closed:
                    raise ManagerError('management_action_cancelled')
                accept(result)
                job['status'] = 'complete'
            except BaseException as error:
                # Neither HTTP exceptions nor private input/employee values enter logs/pages.
                message = INVESTIGATION_FAILURES.get(str(error)) if isinstance(error, ManagerError) else None
                job.update(status='blocked', error=message or 'Action blocked. Review the approved inputs, permissions and verification prerequisites.')
            finally:
                self.lock.release()

        self.thread = threading.Thread(target=work, daemon=True)
        self.thread.start()
        return job['id']

    def close(self):
        self.closed = True
        self.controller.close()
        if self.thread:
            self.thread.join(3)
