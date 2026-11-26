'use strict';

(function () {
  const DEFAULT_API = window.location.port === '8000'
    ? `${window.location.protocol}//${window.location.host}`
    : 'http://127.0.0.1:8000';

  const form = document.getElementById('portalRouterForm');
  const problemInput = document.getElementById('problemInput');
  const findBtn = document.getElementById('findPortalBtn');
  const resultPanel = document.getElementById('portalResultPanel');
  const resPortalName = document.getElementById('resPortalName');
  const resPortalDomain = document.getElementById('resPortalDomain');
  const resPortalReason = document.getElementById('resPortalReason');
  const resRequiredDocs = document.getElementById('resRequiredDocs');
  const resSteps = document.getElementById('resSteps');
  const openPortalBtn = document.getElementById('openPortalBtn');
  const statusPill = document.getElementById('backendStatusPill');

  if (statusPill) {
    statusPill.textContent = `API: ${DEFAULT_API}`;
  }

  let recommendedUrl = '';

  async function queryPortal(problemText) {
    const clean = (problemText || '').trim();
    if (!clean) return;

    findBtn.disabled = true;
    findBtn.textContent = 'Analyzing...';

    try {
      const hasHindi = /[\u0900-\u097F]|\b(mujhe|karna|hai|kaise|chahiye|aay|jati)\b/i.test(clean);
      const resp = await fetch(`${DEFAULT_API}/api/v1/discovery/recommend-portal`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          problem: clean,
          language: hasHindi ? 'hi' : 'en',
        }),
      });

      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status}`);
      }
      const data = await resp.json();
      recommendedUrl = data.portal_url;

      resPortalName.textContent = data.portal_name;
      resPortalDomain.textContent = data.is_local_demo
        ? `local_demo · ${data.domain}`
        : `Verified Official · ${data.domain}`;
      resPortalReason.textContent = data.reason;

      resRequiredDocs.innerHTML = '';
      (data.required_documents || []).forEach((doc) => {
        const span = document.createElement('span');
        span.className = 'doc-pill';
        span.textContent = doc;
        resRequiredDocs.appendChild(span);
      });

      resSteps.innerHTML = '';
      (data.steps || []).forEach((s) => {
        const li = document.createElement('li');
        li.textContent = s.replace(/^\d+\.\s*/, '');
        resSteps.appendChild(li);
      });

      resultPanel.classList.remove('hidden');
    } catch (err) {
      alert(`Could not reach backend (${DEFAULT_API}): ${err.message}`);
    } finally {
      findBtn.disabled = false;
      findBtn.textContent = 'Find Official Portal';
    }
  }

  form.addEventListener('submit', (e) => {
    e.preventDefault();
    queryPortal(problemInput.value);
  });

  document.querySelectorAll('.chip[data-prompt]').forEach((chip) => {
    chip.addEventListener('click', () => {
      const prompt = chip.getAttribute('data-prompt') || '';
      problemInput.value = prompt;
      queryPortal(prompt);
    });
  });

  openPortalBtn.addEventListener('click', () => {
    if (recommendedUrl) {
      window.open(recommendedUrl, '_blank', 'noopener');
    }
  });
})();
