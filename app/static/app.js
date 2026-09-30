'use strict';

const menuToggle = document.querySelector('[data-menu-toggle]');
const nav = document.getElementById('main-nav');
function closeMenu() {
  if (!nav) return;
  nav.classList.remove('open');
  menuToggle.setAttribute('aria-expanded', 'false');
  menuToggle.setAttribute('aria-label', '展开导航');
}
menuToggle?.addEventListener('click', () => {
  const open = nav.classList.toggle('open');
  menuToggle.setAttribute('aria-expanded', String(open));
  menuToggle.setAttribute('aria-label', open ? '收起导航' : '展开导航');
});
document.addEventListener('keydown', event => {
  if (event.key === 'Escape') {
    if (nav?.classList.contains('open')) { closeMenu(); menuToggle.focus(); }
    document.querySelectorAll('.account-menu[open]').forEach(el => el.removeAttribute('open'));
  }
});
document.addEventListener('click', event => {
  document.querySelectorAll('.account-menu[open]').forEach(el => {
    if (!el.contains(event.target)) el.removeAttribute('open');
  });
  if (nav?.classList.contains('open') && !event.target.closest('.sidebar')) closeMenu();
});
document.querySelectorAll('[data-dismiss]').forEach(button => button.addEventListener('click', () => button.closest('.notice').remove()));
document.querySelectorAll('[data-password-toggle]').forEach(button => {
  button.addEventListener('click', () => {
    const input = document.getElementById(button.dataset.passwordToggle);
    const visible = input.type === 'password';
    input.type = visible ? 'text' : 'password';
    button.setAttribute('aria-pressed', String(visible));
    button.setAttribute('aria-label', visible ? '隐藏密码' : '显示密码');
  });
});
document.querySelector('[data-error-summary]')?.focus();

const accountForm = document.querySelector('[data-account-form]');
function validatePlan() {
  if (!accountForm) return true;
  const min = accountForm.elements.min_step;
  const max = accountForm.elements.max_step;
  const start = accountForm.elements.sync_start_hour;
  const end = accountForm.elements.sync_end_hour;
  max.setCustomValidity(Number(min.value) > Number(max.value) ? '最大步数不能小于最小步数。' : '');
  end.setCustomValidity(Number(start.value) > Number(end.value) ? '结束时间不能早于开始时间，暂不支持跨午夜计划。' : '');
  const valid = max.validity.valid && min.validity.valid && end.validity.valid;
  const preview = document.getElementById('plan-preview');
  preview.classList.toggle('invalid', !valid);
  if (!valid) preview.textContent = '请检查步数范围和执行时间，修正后即可保存。';
  else if (!accountForm.elements.is_active.checked) preview.textContent = '自动同步已暂停。保存后可以在账号卡片手动执行。';
  else preview.textContent = '每天 ' + start.value.padStart(2, '0') + ':00–' + end.value.padStart(2, '0') + ':00（北京时间），每小时整点执行，共 ' + (Number(end.value) - Number(start.value) + 1) + ' 次。保存不会立即提交步数。';
  return valid;
}
accountForm?.addEventListener('input', validatePlan);
accountForm?.addEventListener('change', validatePlan);
validatePlan();
const confirmPassword = document.getElementById('confirm_password');
function matchPasswords() {
  if (confirmPassword) confirmPassword.setCustomValidity(confirmPassword.value === document.getElementById('password').value ? '' : '两次输入的密码不一致。');
}
confirmPassword?.addEventListener('input', matchPasswords);
if (confirmPassword) document.getElementById('password').addEventListener('input', matchPasswords);

document.querySelectorAll('[data-loading-form]').forEach(form => {
  form.addEventListener('submit', event => {
    if (form.dataset.busy) { event.preventDefault(); return; }
    if (form === accountForm && !validatePlan()) { event.preventDefault(); form.reportValidity(); return; }
    const button = form.querySelector('button[type="submit"]');
    form.dataset.busy = '1';
    button.dataset.originalText = button.textContent;
    button.textContent = button.dataset.loadingText || '正在提交…';
    button.disabled = true;
    form.querySelector('.form-status').textContent = form === accountForm ? '正在处理，请保持页面打开。验证连接不会提交步数。' : '正在处理，请稍候。';
  });
});
window.addEventListener('pageshow', event => {
  if (event.persisted && !document.querySelector('[data-loading-form]')) {
    window.location.reload();
    return;
  }
  document.querySelectorAll('[data-loading-form][data-busy]').forEach(form => {
    delete form.dataset.busy;
    const button = form.querySelector('button[type="submit"]');
    button.disabled = false;
    button.textContent = button.dataset.originalText;
    form.querySelector('.form-status').textContent = '';
  });
});

const dialog = document.getElementById('confirm-dialog');
let pendingDelete = null;
let deleteTrigger = null;
function bindDeleteForm(form) {
  form.addEventListener('submit', event => {
    if (form.dataset.confirmed === 'yes') return;
    event.preventDefault();
    pendingDelete = form;
    deleteTrigger = form.querySelector('button');
    dialog.showModal();
    dialog.querySelector('[data-confirm-cancel]').focus();
  });
}
document.querySelectorAll('[data-confirm-delete]').forEach(bindDeleteForm);
dialog?.querySelector('[data-confirm-cancel]').addEventListener('click', () => dialog.close());
dialog?.addEventListener('close', () => {
  const menu = deleteTrigger?.closest('details');
  if (menu && !menu.open) menu.querySelector('summary').focus();
  else deleteTrigger?.focus();
});
dialog?.querySelector('[data-confirm-accept]').addEventListener('click', () => {
  if (!pendingDelete) return;
  pendingDelete.dataset.confirmed = 'yes';
  dialog.close();
  pendingDelete.requestSubmit();
});

function showTask(card, message, state, recovery = false) {
  const status = card.querySelector('[data-task-status]');
  const badge = document.createElement('span');
  badge.className = 'badge ' + (state === 'success' ? 'success' : state === 'failed' ? 'danger' : 'info');
  badge.textContent = ({success:'已接受', failed:'执行失败', running:'执行中', queued:'等待执行', unknown:'结果待确认', requires_device:'需要设备', requires_auth:'需要授权', skipped:'已跳过'})[state] || '待确认';
  const text = document.createElement('p');
  text.textContent = message;
  status.replaceChildren(badge, text);
  if (recovery) {
    const link = document.createElement('a');
    link.href = '/accounts';
    link.textContent = '重新加载账号状态';
    status.append(link);
  }
}
function buttonState(card, busy) {
  const button = card.querySelector('[data-sync-form] button');
  button.disabled = busy;
  button.classList.toggle('loading', busy);
  button.querySelector('span').textContent = busy ? '执行中…' : '立即同步';
}
async function requestJson(url, options = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 15000);
  try {
    const response = await fetch(url, {...options, signal: controller.signal, credentials: 'same-origin'});
    if (response.status === 401) throw new Error('登录已过期，请重新登录后查看结果。');
    let data;
    try { data = await response.json(); } catch { throw new Error('服务返回异常，请重新加载页面检查状态。'); }
    if (!response.ok) throw new Error(data.error || '请求失败，请稍后重试。');
    return data;
  } finally { clearTimeout(timer); }
}
async function pollJob(card, url, attempts = 0) {
  try {
    const result = await requestJson(url);
    showTask(card, result.message, result.status);
    if (['success', 'failed', 'requires_auth', 'requires_device', 'unknown', 'skipped'].includes(result.status)) {
      delete card.dataset.pendingJob;
      const response = await fetch('/accounts', {credentials:'same-origin', cache:'no-store', signal:AbortSignal.timeout(15000)});
      if (!response.ok) throw new Error('结果已返回，请重新加载账号状态。');
      const page = new DOMParser().parseFromString(await response.text(), 'text/html');
      const fresh = Array.from(page.querySelectorAll('[data-account-id]')).find(el => el.dataset.accountId === card.dataset.accountId);
      if (!fresh) throw new Error('无法刷新账号卡片，请重新加载页面。');
      card.replaceWith(fresh);
      bindSyncForm(fresh.querySelector('[data-sync-form]'));
      bindDeleteForm(fresh.querySelector('[data-confirm-delete]'));
      return;
    }
    if (attempts >= 160) throw new Error('暂未收到最终结果，请重新加载状态，避免重复提交。');
    window.setTimeout(() => pollJob(card, url, attempts + 1), 2000);
  } catch (error) {
    showTask(card, error.name === 'AbortError' ? '查询超时，任务可能仍在执行。请重新加载状态。' : error.message, 'unknown', true);
    buttonState(card, true);
  }
}
function bindSyncForm(form) {
  form.addEventListener('submit', async event => {
    event.preventDefault();
    const card = form.closest('[data-account-id]');
    if (form.querySelector('button').disabled) return;
    buttonState(card, true);
    showTask(card, '正在提交任务，请稍候…', 'queued');
    try {
      const result = await requestJson(form.action, {method:'POST', headers:{'Content-Type':'application/json','X-CSRF-Token':document.querySelector('meta[name="csrf-token"]').content}, body:'{}'});
      await pollJob(card, result.status_url);
    } catch (error) {
      showTask(card, error.name === 'AbortError' ? '提交响应超时，任务可能已收到。请先重新加载状态。' : error.message, 'unknown', true);
      buttonState(card, true);
    }
  });
}
document.querySelectorAll('[data-sync-form]').forEach(bindSyncForm);
document.querySelectorAll('[data-pending-job]').forEach(card => {buttonState(card, true); pollJob(card, card.dataset.pendingJob);});
