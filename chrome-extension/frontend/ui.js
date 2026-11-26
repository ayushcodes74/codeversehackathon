/**
 * Lightweight UI enhancer for the Google Auth Gate + GPT Copilot Side Panel.
 * Handles frosted popover menu, OCR upload drawer toggle, and execution step counter.
 */
'use strict';

document.addEventListener('DOMContentLoaded', () => {
  const walletMenuBtn = document.getElementById('walletMenuBtn');
  const walletMenuPopover = document.getElementById('walletMenuPopover');
  const toggleUploadDrawerBtn = document.getElementById('toggleUploadDrawerBtn');
  const docVaultDrawer = document.getElementById('docVaultDrawer');
  const planList = document.getElementById('executionPlanList');
  const planCounterPill = document.getElementById('planCounterPill');

  function closePopover() {
    if (!walletMenuPopover || !walletMenuBtn) return;
    walletMenuPopover.classList.add('hidden');
    walletMenuBtn.setAttribute('aria-expanded', 'false');
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

  if (toggleUploadDrawerBtn && docVaultDrawer) {
    toggleUploadDrawerBtn.addEventListener('click', () => {
      const willOpen = docVaultDrawer.classList.contains('hidden');
      docVaultDrawer.classList.toggle('hidden', !willOpen);
      toggleUploadDrawerBtn.textContent = willOpen ? '− Close Upload' : '+ Upload Document (OCR)';
    });
  }

  function syncPlanCounter() {
    if (!planList || !planCounterPill) return;
    const steps = Array.from(planList.querySelectorAll('.plan-step'));
    if (steps.length === 0) {
      planCounterPill.textContent = '0/4';
      return;
    }
    let activeIdx = 0;
    steps.forEach((stepEl, idx) => {
      const txt = (stepEl.textContent || '').toLowerCase();
      const isDone = txt.includes('done') || txt.includes('completed') || txt.includes('✓');
      const isActive = txt.includes('active') || txt.includes('running') || txt.includes('in_progress') || txt.includes('→');
      stepEl.classList.toggle('is-done', isDone);
      stepEl.classList.toggle('is-active', isActive && !isDone);
      if (isActive || isDone) activeIdx = idx + 1;
    });
    planCounterPill.textContent = `${activeIdx}/${steps.length}`;
  }

  if (planList) {
    const observer = new MutationObserver(syncPlanCounter);
    observer.observe(planList, { childList: true, subtree: true, characterData: true });
    syncPlanCounter();
  }
});
