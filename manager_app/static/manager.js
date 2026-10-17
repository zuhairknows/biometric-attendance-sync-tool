'use strict';
// Completion polling exists only while one user-initiated action is running.
// Dashboard, ledger and devices are never refreshed automatically.
const progress = document.querySelector('[data-job-status]');
if (progress) {
  const label = progress.querySelector('[data-job-label]');
  const next = progress.querySelector('[data-job-next]');
  async function check() {
    try {
      const response = await fetch(progress.dataset.jobStatus, {cache: 'no-store'});
      if (!response.ok) throw new Error();
      const result = await response.json();
      if (result.status === 'running') {
        next.hidden = true;
        setTimeout(check, 1000);
      } else {
        label.textContent = result.status === 'complete' ? 'Action complete' : 'Action blocked';
        next.hidden = false;
        if (result.status === 'blocked') progress.querySelector('[data-job-error]').textContent =
          'Review the approved inputs, permissions and verification prerequisites.';
      }
    } catch (_) {
      label.textContent = 'Manager unavailable';
      next.hidden = false;
      progress.querySelector('[data-job-error]').textContent = 'Reconnect to the manager to review the outcome. The Windows service is independent.';
    }
  }
  check();
}
const scope = document.querySelector('[data-investigation]');
if (scope) {
  scope.querySelector('[data-floor]').addEventListener('change', (event) => {
    for (const label of scope.querySelectorAll('[data-device-floor]')) {
      label.hidden = Boolean(event.target.value && label.dataset.deviceFloor !== event.target.value);
      if (label.hidden) label.querySelector('input').checked = false;
    }
  });
}
for (const form of document.querySelectorAll('form[method="post"]')) {
  // The browser has already constructed the POST data when this event fires.
  // Clear the original widgets without removing credentials from that one request.
  form.addEventListener('formdata', () => {
    for (const input of form.querySelectorAll('input[type="password"]')) input.value = '';
  });
  form.addEventListener('submit', () => {
    const button = form.querySelector('button');
    if (button) { button.disabled = true; button.textContent = 'Starting…'; }
  });
}
window.addEventListener('pageshow', () => {
  for (const input of document.querySelectorAll('input[type="password"]')) input.value = '';
});
