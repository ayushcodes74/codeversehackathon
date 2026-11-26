/**
 * Lightweight UI enhancer for the Dark Glassmorphism Crypto Wallet Extension Side Panel.
 * Handles frosted popover menu, drawers, account pill sync, and hero status updates
 * without modifying sidepanel.js API or CryptoVault logic.
 */
'use strict';

document.addEventListener('DOMContentLoaded', () => {
  const accountPillBtn = document.getElementById('accountPillBtn');
  const accountPillLabel = document.getElementById('accountPillLabel');
  const sessionDrawer = document.getElementById('sessionVaultDrawer');
  const drawerCloseHint = document.getElementById('drawerCloseHint');

  const walletMenuBtn = document.getElementById('walletMenuBtn');
  const walletMenuPopover = document.getElementById('walletMenuPopover');
  const menuOpenSessionBtn = document.getElementById('menuOpenSessionBtn');
  const menuOpenVaultBtn = document.getElementById('menuOpenVaultBtn');
  const menuVerifyPortalBtn = document.getElementById('menuVerifyPortalBtn');
  const menuToggleLangBtn = document.getElementById('menuToggleLangBtn');
  const menuLockVaultBtn = document.getElementById('menuLockVaultBtn');
  const menuLangLabel = document.getElementById('menuLangLabel');

  const vaultToggle = document.getElementById('vaultDrawerToggle');
  const docDrawer = document.getElementById('docVaultDrawer');

  const authBadge = document.getElementById('authStatusBadge');
  const userIdInput = document.getElementById('userIdInput');
  const userFullNameInput = document.getElementById('userFullNameInput');
  const vaultPassphraseInput = document.getElementById('vaultPassphraseInput');
  const domainBadge = document.getElementById('domainShieldBadge');
  const taskBadge = document.getElementById('taskStateBadge');
  const planList = document.getElementById('executionPlanList');
  const planCounterPill = document.getElementById('planCounterPill');
  const heroHeadline = document.getElementById('heroStatusHeadline');
  const langEnBtn = document.getElementById('langEnBtn');
  const langHiBtn = document.getElementById('langHiBtn');
  const refreshShieldBtn = document.getElementById('refreshShieldBtn');
  const lockVaultBtn = document.getElementById('lockVaultBtn');

  function setDrawer(panel, btn, open) {
    if (!panel) return;
    panel.classList.toggle('hidden', !open);
    if (btn) btn.setAttribute('aria-expanded', open ? 'true' : 'false');
  }

  function closePopover() {
    if (!walletMenuPopover || !walletMenuBtn) return;
    walletMenuPopover.classList.add('hidden');
    walletMenuBtn.setAttribute('aria-expanded', 'false');
  }

  if (accountPillBtn && sessionDrawer) {
    accountPillBtn.addEventListener('click', () => {
      const isHidden = sessionDrawer.classList.contains('hidden');
      setDrawer(sessionDrawer, accountPillBtn, isHidden);
      closePopover();
    });
  }

  if (drawerCloseHint && sessionDrawer) {
    drawerCloseHint.addEventListener('click', () => {
      setDrawer(sessionDrawer, accountPillBtn, false);
    });
  }

  if (walletMenuBtn && walletMenuPopover) {
    walletMenuBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      const willOpen = walletMenuPopover.classList.contains('hidden');
      walletMenuPopover.classList.toggle('hidden', !willOpen);
      walletMenuBtn.setAttribute('aria-expanded', willOpen ? 'true' : 'false');
    });

    document.addEventListener('click', (e) => {
      if (!walletMenuPopover.contains(e.target) && !walletMenuBtn.contains(e.target)) {
        closePopover();
      }
    });
  }

  if (menuOpenSessionBtn) {
    menuOpenSessionBtn.addEventListener('click', () => {
      setDrawer(sessionDrawer, accountPillBtn, true);
      closePopover();
      if (userFullNameInput) userFullNameInput.focus();
    });
  }

  if (menuOpenVaultBtn) {
    menuOpenVaultBtn.addEventListener('click', () => {
      setDrawer(sessionDrawer, accountPillBtn, true);
      closePopover();
      if (vaultPassphraseInput) vaultPassphraseInput.focus();
    });
  }

  if (menuVerifyPortalBtn && refreshShieldBtn) {
    menuVerifyPortalBtn.addEventListener('click', () => {
      closePopover();
      refreshShieldBtn.click();
    });
  }

  if (menuToggleLangBtn) {
    menuToggleLangBtn.addEventListener('click', () => {
      const isHi = langHiBtn && langHiBtn.classList.contains('active');
      if (isHi && langEnBtn) langEnBtn.click();
      else if (langHiBtn) langHiBtn.click();
      closePopover();
    });
  }

  if (menuLockVaultBtn && lockVaultBtn) {
    menuLockVaultBtn.addEventListener('click', () => {
      closePopover();
      lockVaultBtn.click();
    });
  }

  if (vaultToggle && docDrawer) {
    vaultToggle.addEventListener('click', () => {
      const isHidden = docDrawer.classList.contains('hidden');
      setDrawer(docDrawer, vaultToggle, isHidden);
    });
  }

  function syncWalletUI() {
    // Sync Account Pill label with User Name or truncated User ID
    if (accountPillLabel) {
      const uid = userIdInput ? userIdInput.value.trim() : '';
      const fullName = userFullNameInput ? userFullNameInput.value.trim() : '';
      if (uid) {
        accountPillLabel.textContent = fullName ? `${fullName} · ${uid.slice(0, 6)}…` : `${uid.slice(0, 8)}…`;
      } else if (fullName) {
        accountPillLabel.textContent = fullName;
      } else if (authBadge && authBadge.classList.contains('ok')) {
        accountPillLabel.textContent = authBadge.textContent.trim();
      }
    }

    // Sync menu language indicator
    if (menuLangLabel && langHiBtn) {
      menuLangLabel.textContent = langHiBtn.classList.contains('active') ? 'Hinglish' : 'EN';
    }

    // Sync Execution Plan step rows & Hero Status Headline
    const steps = planList ? Array.from(planList.querySelectorAll('.plan-step')) : [];
    let activeIdx = 0;
    steps.forEach((stepEl, idx) => {
      const txt = (stepEl.textContent || '').toLowerCase();
      const isDone = txt.includes('done') || txt.includes('completed') || txt.includes('✓');
      const isActive = txt.includes('active') || txt.includes('running') || txt.includes('in_progress') || txt.includes('→');
      stepEl.classList.toggle('is-done', isDone);
      stepEl.classList.toggle('is-active', isActive && !isDone);
      if (isActive || isDone) activeIdx = idx + 1;
    });

    const total = steps.length || 4;
    if (planCounterPill) {
      planCounterPill.textContent = `${activeIdx}/${total}`;
    }

    if (heroHeadline) {
      const stateTxt = taskBadge ? (taskBadge.textContent || 'Idle').trim() : 'Idle';
      if (steps.length > 0 || (stateTxt && stateTxt !== 'Idle')) {
        heroHeadline.textContent = `${stateTxt === 'Idle' ? 'Ready' : stateTxt} · ${activeIdx}/${total} Steps`;
      } else if (domainBadge && domainBadge.classList.contains('ok')) {
        const parts = (domainBadge.textContent || '').split(':');
        const host = parts.length > 1 ? parts.slice(1).join(':').trim() : domainBadge.textContent.trim();
        heroHeadline.textContent = host || 'Portal Verified';
      } else {
        heroHeadline.textContent = 'Ready · 0/4 Steps';
      }
    }
  }

  const observer = new MutationObserver(syncWalletUI);
  [authBadge, domainBadge, taskBadge, planList, langEnBtn, langHiBtn].forEach((node) => {
    if (node) observer.observe(node, { childList: true, subtree: true, characterData: true, attributes: true });
  });
  if (userIdInput) userIdInput.addEventListener('input', syncWalletUI);
  if (userFullNameInput) userFullNameInput.addEventListener('input', syncWalletUI);

  syncWalletUI();
});
