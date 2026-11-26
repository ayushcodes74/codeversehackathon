/**
 * Bilingual (EN / Hinglish) Side Panel Controller for Universal AI Web Assistant.
 *
 * Strictly aligned verbatim to `chrome-extension/background.js` message contracts:
 * - SET_VAULT_PASSPHRASE
 * - AUTH_DEV_LOGIN
 * - VERIFY_PORTAL
 * - UPLOAD_AND_EXTRACT_DOC
 * - SAVE_VERIFIED_DOC
 * - LIST_VAULT_DOCS
 * - AGENT_CHAT_STEP
 * - APPROVE_TASK
 *
 * Content-script messages:
 * - CAPTURE_DOM_SNAPSHOT (dom_inspector.js)
 * - EXECUTE_APPROVED_ACTIONS (action_executor.js)
 * - CAPTURE_RECEIPT (action_executor.js)
 */

'use strict';

const DEFAULT_PLAN_STEPS = [
  { step: 1, titleEn: 'Personal Details', titleHi: 'व्यक्तिगत विवरण' },
  { step: 2, titleEn: 'Address Info', titleHi: 'पते की जानकारी' },
  { step: 3, titleEn: 'Documents & Bank', titleHi: 'दस्तावेज़ एवं बैंक खाता' },
  { step: 4, titleEn: 'Review & Submit', titleHi: 'समीक्षा एवं जमा' },
];

const I18N = {
  en: {
    appTitle: 'Universal AI Assistant',
    sessionVaultTitle: 'Session & Domain Shield',
    apiUrlLabel: 'Backend API URL',
    userIdLabel: 'User ID (Optional UUID)',
    fullNameLabel: 'Full Name',
    emailLabel: 'Email',
    connectBtn: 'Connect / Dev Login',
    recheckDomainBtn: 'Verify Portal',
    passphraseLabel: 'Local CryptoVault Passphrase',
    unlockBtn: 'Unlock',
    lockBtn: 'Lock',
    agentChatTitle: 'Chat, Plan & Visual Guide',
    chatEmpty: 'Submit a goal or ask a visual guide question with the active portal tab.',
    executionPlanTitle: 'Execution Plan',
    goalLabel: 'Goal or Question',
    runStepBtn: 'Run Step',
    visualGuideBtn: 'Visual Guide Q&A',
    reviewApprovalBtn: 'Review & Approve',
    clarificationTitle: 'Clarification Needed',
    sendClarificationBtn: 'Send Answer',
    docVaultTitle: 'Document Upload & Verification (FR-037)',
    docTypeLabel: 'Document Type',
    docFileLabel: 'Select Image or PDF',
    extractBtn: 'Upload & Extract (4-Stage)',
    docPassphraseLabel: 'Vault Passphrase (if not unlocked above)',
    commitVaultBtn: 'Save Verified to Vault',
    reviewCardTitle: 'Action-Bound Approval Required (FR-027)',
    receiptTitle: 'Submission Receipt (FR-061)',
    receiptHint: 'After final submission, confirmed application_ref_no or unconfirmed warning appears here.',
    verifyReceiptBtn: 'Scan & Verify Receipt',
    approvalModalTitle: 'Action-Bound Review & Approval',
    targetDomainLabel: 'Verified Domain',
    actionHashLabel: 'Action-Bound SHA-256 Hash (pending_action_hash)',
    mappedFieldsLabel: 'Mapped Fields / Actions',
    rejectBtn: 'Reject',
    approveSubmitBtn: 'Approve & Submit',
  },
  hi: {
    appTitle: 'यूनिवर्सल AI वेब सहायक (EN / Hinglish)',
    sessionVaultTitle: 'सेशन और डोमेन शील्ड',
    apiUrlLabel: 'बैकएंड API URL',
    userIdLabel: 'यूज़र ID (वैकल्पिक UUID)',
    fullNameLabel: 'पूरा नाम',
    emailLabel: 'ईमेल',
    connectBtn: 'कनेक्ट / देव लॉगिन',
    recheckDomainBtn: 'पोर्टल जांचें',
    passphraseLabel: 'लोकल वॉल्ट पासफ़्रेज़',
    unlockBtn: 'अनलॉक करें',
    lockBtn: 'लॉक करें',
    agentChatTitle: 'चैट, प्लान और विज़ुअल गाइड',
    chatEmpty: 'वर्तमान पोर्टल टैब पर लक्ष्य भेजें या विज़ुअल गाइड प्रश्न पूछें।',
    executionPlanTitle: 'निष्पादन योजना (Execution Plan)',
    goalLabel: 'लक्ष्य या सवाल',
    runStepBtn: 'स्टेप चलाएं',
    visualGuideBtn: 'विज़ुअल गाइड Q&A',
    reviewApprovalBtn: 'समीक्षा और स्वीकृति दें',
    clarificationTitle: 'स्पष्टीकरण आवश्यक',
    sendClarificationBtn: 'उत्तर भेजें',
    docVaultTitle: 'दस्तावेज़ अपलोड और सत्यापन (FR-037)',
    docTypeLabel: 'दस्तावेज़ प्रकार',
    docFileLabel: 'इमेज या PDF चुनें',
    extractBtn: 'अपलोड और एक्सट्रैक्ट करें (4-Stage)',
    docPassphraseLabel: 'वॉल्ट पासफ़्रेज़ (यदि ऊपर अनलॉक नहीं है)',
    commitVaultBtn: 'सत्यापित फ़ील्ड वॉल्ट में सेव करें',
    reviewCardTitle: 'कार्रवाई-बद्ध स्वीकृति आवश्यक (FR-027)',
    receiptTitle: 'रसीद सत्यापन (FR-061)',
    receiptHint: 'अंतिम सबमिट के बाद सत्यापित application_ref_no या अपुष्ट चेतावनी यहाँ दिखेगी।',
    verifyReceiptBtn: 'रसीद स्कैन और सत्यापित करें',
    approvalModalTitle: 'कार्रवाई-बद्ध समीक्षा और मानव स्वीकृति',
    targetDomainLabel: 'सत्यापित डोमेन (Verified Domain)',
    actionHashLabel: 'Action-Bound SHA-256 Hash (pending_action_hash)',
    mappedFieldsLabel: 'मैप किए गए फ़ील्ड / एक्शन',
    rejectBtn: 'अस्वीकार करें',
    approveSubmitBtn: 'स्वीकृति दें और सबमिट करें',
  },
};

const state = {
  lang: 'en',
  apiBaseUrl: 'http://127.0.0.1:8000',
  userId: '',
  profileId: '',
  profileSlug: 'self',
  authenticated: false,
  vaultUnlocked: false,
  activeTabId: null,
  activeUrl: '',
  activeDomain: '',
  domainVerdict: null,
  taskId: null,
  approvedActionHash: null,
  currentStepIndex: 0,
  totalSteps: 4,
  taskStatus: 'Idle',
  nextTool: null,
  lastExtraction: null,
  lastRawFileBase64: '',
  lastMimeType: '',
  pendingApproval: null,
};

function el(id) {
  return document.getElementById(id);
}

function getApiBaseUrl() {
  return String((el('apiBaseUrlInput') && el('apiBaseUrlInput').value) || state.apiBaseUrl || 'http://127.0.0.1:8000')
    .trim()
    .replace(/\/+$/, '');
}

/**
 * Sends a message to `background.js` matching its `{ action, ... }` and `{ ok: true, ... }` contract.
 */
function sendBg(action, payload = {}) {
  return new Promise((resolve, reject) => {
    chrome.runtime.sendMessage({ action, ...payload }, (response) => {
      if (chrome.runtime.lastError) {
        reject(new Error(chrome.runtime.lastError.message));
        return;
      }
      if (!response || response.ok !== true) {
        reject(new Error((response && response.error) || `Background action '${action}' failed.`));
        return;
      }
      resolve(response);
    });
  });
}

function showAlert(message, kind = 'warn') {
  const banner = el('globalAlert');
  banner.textContent = message;
  banner.className = `status-banner show ${kind}`;
}

function clearAlert() {
  const banner = el('globalAlert');
  banner.className = 'status-banner';
  banner.textContent = '';
}

function applyLanguage(lang) {
  state.lang = lang === 'hi' ? 'hi' : 'en';
  el('langEnBtn').classList.toggle('active', state.lang === 'en');
  el('langHiBtn').classList.toggle('active', state.lang === 'hi');
  const dict = I18N[state.lang];
  document.querySelectorAll('[data-i18n]').forEach((node) => {
    const key = node.getAttribute('data-i18n');
    if (key && dict[key]) {
      node.textContent = dict[key];
    }
  });
  renderExecutionPlan(state.currentStepIndex, state.totalSteps, state.taskStatus, []);
}

function appendFeed(role, textEn, textHi) {
  const feed = el('chatFeed');
  if (feed.querySelector('.muted')) {
    feed.innerHTML = '';
  }
  const div = document.createElement('div');
  div.className = 'feed-msg';
  const body = state.lang === 'hi' && textHi ? textHi : textEn;
  const strong = document.createElement('strong');
  strong.textContent = `${role}: `;
  const span = document.createElement('span');
  span.textContent = body || '';
  div.appendChild(strong);
  div.appendChild(span);
  feed.appendChild(div);
  feed.scrollTop = feed.scrollHeight;
}

async function getActiveTabInfo() {
  const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  const tab = tabs && tabs[0];
  if (!tab) return null;
  state.activeTabId = tab.id;
  state.activeUrl = tab.url || '';
  try {
    state.activeDomain = state.activeUrl ? new URL(state.activeUrl).hostname : '';
  } catch (_err) {
    state.activeDomain = '';
  }
  return tab;
}

function captureActiveTabDom() {
  return new Promise((resolve) => {
    if (!state.activeTabId) {
      resolve(null);
      return;
    }
    chrome.tabs.sendMessage(state.activeTabId, { type: 'CAPTURE_DOM_SNAPSHOT' }, (resp) => {
      if (chrome.runtime.lastError || !resp || (!resp.ok && !resp.success)) {
        resolve(null);
        return;
      }
      resolve(resp.snapshot || null);
    });
  });
}

function captureActiveTabReceipt() {
  return new Promise((resolve) => {
    if (!state.activeTabId) {
      resolve(null);
      return;
    }
    chrome.tabs.sendMessage(state.activeTabId, { type: 'CAPTURE_RECEIPT', action: 'CAPTURE_RECEIPT' }, (resp) => {
      if (chrome.runtime.lastError || !resp || (!resp.ok && !resp.success)) {
        resolve(null);
        return;
      }
      resolve(resp.receipt || null);
    });
  });
}

function dispatchActionsToActiveTab(actions, taskId, actionHash) {
  return new Promise((resolve, reject) => {
    if (!state.activeTabId) {
      reject(new Error('No active browser tab found.'));
      return;
    }
    chrome.tabs.sendMessage(
      state.activeTabId,
      {
        type: 'EXECUTE_APPROVED_ACTIONS',
        action: 'EXECUTE_APPROVED_ACTIONS',
        task_id: taskId,
        action_hash: actionHash,
        actions,
      },
      (resp) => {
        if (chrome.runtime.lastError) {
          reject(new Error(chrome.runtime.lastError.message));
          return;
        }
        if (!resp || (!resp.ok && !resp.success)) {
          reject(new Error((resp && resp.error) || 'Tab action execution failed.'));
          return;
        }
        resolve(resp);
      }
    );
  });
}

// ---------------------------------------------------------------------------
// View 1: Session, Language & Domain Shield Header
// ---------------------------------------------------------------------------

async function refreshDomainShield() {
  const badge = el('domainShieldBadge');
  const reasonNode = el('domainReasonText');
  await getActiveTabInfo();

  if (!state.activeUrl || (!state.activeUrl.startsWith('http://') && !state.activeUrl.startsWith('https://'))) {
    badge.textContent = 'No Portal Tab';
    badge.className = 'badge warn';
    reasonNode.textContent = '';
    return;
  }

  try {
    const verdict = await sendBg('VERIFY_PORTAL', {
      url: state.activeUrl,
      apiBaseUrl: getApiBaseUrl(),
    });
    state.domainVerdict = verdict;

    if (verdict.is_official) {
      badge.textContent = `${verdict.support_tier || 'verified_official'}: ${verdict.canonical_domain || state.activeDomain}`;
      badge.className = 'badge ok';
      reasonNode.textContent = verdict.reason || '';
    } else if (state.activeDomain === 'localhost' || state.activeDomain === '127.0.0.1') {
      badge.textContent = `local_demo: ${state.activeDomain}`;
      badge.className = 'badge info';
      reasonNode.textContent = 'Local synthetic demo portal allowed for testing.';
    } else {
      badge.textContent = `blocked: ${verdict.canonical_domain || state.activeDomain}`;
      badge.className = 'badge err';
      reasonNode.textContent = verdict.reason || 'Domain not on approved portal allowlist.';
    }
  } catch (err) {
    badge.textContent = 'Shield Offline';
    badge.className = 'badge warn';
    reasonNode.textContent = err.message;
  }
}

async function handleConnectDevLogin() {
  clearAlert();
  const apiBaseUrl = getApiBaseUrl();
  state.apiBaseUrl = apiBaseUrl;

  let userId = (el('userIdInput').value || '').trim();
  let profileId = state.profileId || '';

  if (!userId) {
    const fullName = (el('userFullNameInput').value || '').trim() || 'Citizen User';
    const email = (el('userEmailInput').value || '').trim() || `citizen_${Date.now()}@example.com`;
    const authResp = await fetch(`${apiBaseUrl}/api/v1/users/auth`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        google_sub: `ext_${email.toLowerCase()}`,
        email,
        full_name: fullName,
        preferred_language: state.lang,
      }),
    });
    if (!authResp.ok) {
      const text = await authResp.text();
      throw new Error(`User init failed (HTTP ${authResp.status}): ${text}`);
    }
    const userData = await authResp.json();
    userId = userData.user_id;
    profileId = userData.profiles && userData.profiles[0] ? userData.profiles[0].id : '';
    el('userIdInput').value = userId;
  }

  const loginRes = await sendBg('AUTH_DEV_LOGIN', {
    apiBaseUrl,
    user_id: userId,
    profile_id: profileId || undefined,
  });

  state.userId = loginRes.user_id || userId;
  state.profileId = profileId;
  state.authenticated = true;

  const authBadge = el('authStatusBadge');
  authBadge.textContent = `Connected (${String(state.userId).slice(0, 8)}...)`;
  authBadge.className = 'badge ok';
  showAlert('Authenticated via AUTH_DEV_LOGIN and HS256 JWT stored in extension session.', 'ok');
}

async function refreshVaultList(passphraseOverride = undefined) {
  try {
    const res = await sendBg('LIST_VAULT_DOCS', {
      profileId: state.profileSlug || 'self',
      passphrase: passphraseOverride,
    });
    const docs = res.docs || [];
    const node = el('vaultRecordsList');
    if (docs.length === 0) {
      node.textContent =
        state.lang === 'hi'
          ? 'वॉल्ट में कोई एन्क्रिप्टेड दस्तावेज़ नहीं मिला।'
          : 'No encrypted documents saved in extension CryptoVault yet.';
      return;
    }
    const summary = docs
      .map((d) => {
        const keys = Object.entries(d.maskedFields || {})
          .map(([k, info]) => `${k}: ${info.maskedValue}`)
          .join(', ');
        return `${d.docType}${keys ? ` [${keys}]` : ''}`;
      })
      .join(' | ');
    node.textContent =
      (state.lang === 'hi' ? 'वॉल्ट दस्तावेज़: ' : 'Encrypted Vault Docs: ') + summary;
  } catch (_err) {
    // Passphrase may be locked or IndexedDB empty.
  }
}

// ---------------------------------------------------------------------------
// View 2: Chat, Execution Plan, Clarification & Visual Guide
// ---------------------------------------------------------------------------

function renderExecutionPlan(currentStepIndex, totalSteps, statusText, actions) {
  const box = el('executionPlanBox');
  const list = el('executionPlanList');
  if (!state.taskId && (!actions || actions.length === 0)) {
    box.classList.add('hidden');
    return;
  }
  box.classList.remove('hidden');
  const total = totalSteps || DEFAULT_PLAN_STEPS.length;
  const activeStep = currentStepIndex || 1;

  const stepRows = DEFAULT_PLAN_STEPS.slice(0, total).map((item) => {
    let badgeClass = 'badge';
    let label = 'pending';
    if (statusText === 'submitted' || item.step < activeStep) {
      badgeClass = 'badge ok';
      label = 'done';
    } else if (item.step === activeStep) {
      badgeClass = 'badge info';
      label = statusText || 'active';
    }
    const title = state.lang === 'hi' ? item.titleHi : item.titleEn;
    return `<div class="plan-step"><span>${item.step}. ${title}</span><span class="${badgeClass}">${label}</span></div>`;
  });

  if (Array.isArray(actions) && actions.length > 0) {
    const actionSummary = actions
      .map((a) => `${a.action_type}: ${a.description || a.selector || a.element_key || ''}`)
      .join(' • ');
    stepRows.push(`<div class="muted" style="margin-top:4px;">Queued actions: ${actionSummary}</div>`);
  }

  list.innerHTML = stepRows.join('');
}

function renderClarification(step) {
  const box = el('clarificationBox');
  const qNode = el('clarificationQuestion');
  const optsNode = el('clarificationOptions');

  if (!step || step.response_type !== 'clarification') {
    box.classList.add('hidden');
    optsNode.innerHTML = '';
    return;
  }

  box.classList.remove('hidden');
  const clar = step.clarification || step.tool_input || {};
  const questionText =
    (state.lang === 'hi' ? clar.question_hi || step.message_hi : clar.question_en || step.message) ||
    step.message ||
    'Please provide additional input:';
  qNode.textContent = questionText;
  optsNode.innerHTML = '';

  const opts = Array.isArray(clar.options) ? clar.options : [];
  opts.forEach((opt) => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'option-chip';
    btn.textContent = String(opt);
    btn.addEventListener('click', () => {
      el('clarificationInput').value = String(opt);
      runAgentTurn(String(opt)).catch((err) => showAlert(err.message, 'err'));
    });
    optsNode.appendChild(btn);
  });
}

function renderVisualGuide(card) {
  const box = el('visualGuideCard');
  if (!card) {
    box.classList.add('hidden');
    return;
  }
  box.classList.remove('hidden');
  el('vgTitle').textContent = state.lang === 'hi' && card.title_hi ? card.title_hi : card.title;
  el('vgSampleBadge').textContent = card.document_sample_id || 'guide';
  el('vgExplanation').textContent =
    state.lang === 'hi' && card.explanation_hi ? card.explanation_hi : card.explanation;

  const calloutsNode = el('vgCallouts');
  calloutsNode.innerHTML = '';
  (card.bounding_box_callouts || []).forEach((c) => {
    const div = document.createElement('div');
    div.className = 'callout';
    const lbl = state.lang === 'hi' && c.label_hi ? c.label_hi : c.label;
    const desc = state.lang === 'hi' && c.description_hi ? c.description_hi : c.description;
    div.textContent = `${lbl}: ${desc}`;
    calloutsNode.appendChild(div);
  });
}

// ---------------------------------------------------------------------------
// View 4: Action-Bound Review Card/Modal & Receipt (FR-027 & FR-061)
// ---------------------------------------------------------------------------

function renderReceipt(receiptData, status) {
  const badge = el('receiptBadge');
  const body = el('receiptBody');
  const refNo =
    receiptData &&
    (receiptData.receipt_number ||
      (receiptData.captured_data && receiptData.captured_data.application_ref_no));
  const isConfirmed = Boolean(receiptData && receiptData.is_success !== false && refNo);

  if (isConfirmed) {
    badge.textContent = 'Confirmed';
    badge.className = 'badge ok';
    body.className = 'status-banner show ok';
    body.textContent =
      state.lang === 'hi'
        ? `सत्यापित आवेदन संदर्भ संख्या (application_ref_no): ${refNo} (${status || 'submitted'})`
        : `Confirmed application_ref_no: ${refNo} (Status: ${status || 'submitted'})`;
  } else {
    badge.textContent = 'Unconfirmed';
    badge.className = 'badge warn';
    body.className = 'status-banner show warn';
    body.textContent =
      state.lang === 'hi'
        ? 'चेतावनी (FR-061): कोई सत्यापित application_ref_no नहीं मिला। सबमिशन अपुष्ट है।'
        : 'Unconfirmed (FR-061): No verified application_ref_no found on page yet.';
  }
}

function renderMappedPreviewRows(container, actions, summary) {
  container.innerHTML = '';
  if (Array.isArray(actions) && actions.length > 0) {
    actions.forEach((act) => {
      const item = document.createElement('div');
      item.className = 'field-item';
      const meta = document.createElement('div');
      meta.className = 'field-meta';
      const k = document.createElement('div');
      k.className = 'field-key';
      k.textContent = `${act.action_type}: ${act.description || act.selector || act.element_key || ''}`;
      const v = document.createElement('div');
      v.className = 'field-val';
      v.textContent = act.value_or_vault_ref || act.selector || '';
      meta.appendChild(k);
      meta.appendChild(v);
      item.appendChild(meta);
      container.appendChild(item);
    });
  } else if (summary && typeof summary === 'object') {
    Object.entries(summary).forEach(([k, v]) => {
      const item = document.createElement('div');
      item.className = 'field-item';
      item.textContent = `${k}: ${String(v)}`;
      container.appendChild(item);
    });
  }
}

function showActionBoundReview(approvalMeta) {
  state.pendingApproval = approvalMeta;

  // Inline card
  el('actionBoundReviewCard').classList.remove('hidden');
  el('inlineReviewDomain').textContent = approvalMeta.targetDomain || state.activeDomain || 'unknown';
  el('inlineReviewHash').textContent = approvalMeta.actionHash || '';
  renderMappedPreviewRows(el('inlineReviewFields'), approvalMeta.actions, approvalMeta.summary);

  // Modal
  el('approvalTargetDomain').textContent = approvalMeta.targetDomain || state.activeDomain || 'unknown';
  el('approvalActionHash').textContent = approvalMeta.actionHash || '';
  el('approvalModalSubtitle').textContent =
    state.lang === 'hi'
      ? 'अंतिम सबमिट से पहले कृपया सत्यापित डोमेन और pending_action_hash की पुष्टि करें (FR-027)।'
      : 'Verify target domain, mapped fields, and pending_action_hash before approving submission (FR-027).';
  renderMappedPreviewRows(el('approvalMappedFields'), approvalMeta.actions, approvalMeta.summary);
  el('approvalModalBackdrop').classList.add('open');
  el('reviewPendingBtn').classList.remove('hidden');
}

function hideActionBoundReview() {
  el('approvalModalBackdrop').classList.remove('open');
  el('actionBoundReviewCard').classList.add('hidden');
  el('reviewPendingBtn').classList.add('hidden');
  state.pendingApproval = null;
}

async function submitApprovalDecision(approved) {
  const pending = state.pendingApproval;
  if (!pending || !pending.taskId || !pending.actionHash) {
    hideActionBoundReview();
    return;
  }

  clearAlert();
  const res = await sendBg('APPROVE_TASK', {
    apiBaseUrl: getApiBaseUrl(),
    tabId: state.activeTabId,
    task_id: pending.taskId,
    action_hash: pending.actionHash,
    approved: Boolean(approved),
    reviewer_note: approved ? 'Approved via Side Panel' : 'Rejected via Side Panel',
    actions: Array.isArray(pending.actions) ? pending.actions : [],
  });

  const nextStatus = res.approval && res.approval.status;
  if (nextStatus) {
    state.taskStatus = nextStatus;
    el('taskStateBadge').textContent = `${nextStatus} (${state.currentStepIndex}/${state.totalSteps})`;
  }

  if (!approved) {
    hideActionBoundReview();
    showAlert('Task submission rejected and cancelled.', 'warn');
    return;
  }

  state.approvedActionHash = pending.actionHash;
  const approvedTaskId = pending.taskId;
  hideActionBoundReview();

  // Re-run agent turn on current DOM so orchestrator emits verified CLICK_BUTTON for submit.
  await getActiveTabInfo();
  const domSnapshot = await captureActiveTabDom();
  const nextStep = await sendBg('AGENT_CHAT_STEP', {
    apiBaseUrl: getApiBaseUrl(),
    user_id: state.userId,
    profile_id: state.profileId || undefined,
    task_id: approvedTaskId,
    message: 'Proceed with approved submission',
    language: state.lang,
    current_url: state.activeUrl,
    dom_snapshot: domSnapshot,
  });
  await handleAgentResponse(nextStep);
}

async function handleAgentResponse(step) {
  if (step.task_id) {
    state.taskId = step.task_id;
  }
  state.currentStepIndex = step.current_step_index || state.currentStepIndex || 1;
  state.totalSteps = step.total_steps || state.totalSteps || 4;
  state.taskStatus = step.status || 'in_progress';
  state.nextTool = step.next_tool || null;

  el('taskStateBadge').textContent = `${state.taskStatus} (${state.currentStepIndex}/${state.totalSteps})`;

  const toolBadge = el('activeToolBadge');
  if (state.nextTool) {
    toolBadge.textContent = `tool: ${state.nextTool}`;
    toolBadge.classList.remove('hidden');
  } else {
    toolBadge.classList.add('hidden');
  }

  appendFeed('Assistant', step.message, step.message_hi);
  const actions = step.actions || step.client_actions || [];
  renderExecutionPlan(state.currentStepIndex, state.totalSteps, state.taskStatus, actions);
  renderClarification(step);

  if (step.visual_guide) {
    renderVisualGuide(step.visual_guide);
  }

  if (step.response_type === 'completed' || step.receipt_data) {
    renderReceipt(step.receipt_data, step.status);
  }

  // Action-Bound Review Gate when pending_action_hash is present (FR-027)
  if (step.pending_action_hash) {
    showActionBoundReview({
      taskId: state.taskId,
      targetDomain: state.activeDomain,
      actionHash: step.pending_action_hash,
      actions,
      summary:
        (step.approval_request &&
          (step.approval_request.summary_of_changes || step.approval_request.action_payload)) ||
        {},
    });
    return;
  }

  // Execute approved or non-vault actions on active tab
  if (step.response_type === 'action' && Array.isArray(actions) && actions.length > 0 && state.activeTabId) {
    const hasVaultToken = actions.some(
      (a) => a && typeof a.value_or_vault_ref === 'string' && a.value_or_vault_ref.startsWith('vault:')
    );
    if (!hasVaultToken || state.approvedActionHash) {
      try {
        const execOutcome = await dispatchActionsToActiveTab(
          actions,
          state.taskId,
          state.approvedActionHash
        );
        const okCount = (execOutcome.results || []).filter((r) => r.success).length;
        const verifiedMsg =
          typeof execOutcome.verified_count === 'number'
            ? ` (verified ${execOutcome.verified_count}, mismatches ${(execOutcome.mismatches || []).length})`
            : '';
        showAlert(`Executed ${okCount} action(s) on active tab${verifiedMsg}.`, 'ok');

        // If CLICK_BUTTON submitted the form, scan & verify receipt automatically.
        if (actions.some((a) => a.action_type === 'CLICK_BUTTON')) {
          await new Promise((r) => setTimeout(r, 250));
          await verifySubmissionReceipt();
        }
      } catch (err) {
        showAlert(err.message, 'warn');
      }
    }
  }
}

async function runAgentTurn(messageOverride = null, { includeDom = true } = {}) {
  clearAlert();
  if (!state.authenticated || !state.userId) {
    showAlert('Click "Connect / Dev Login" before running an agent step.', 'warn');
    return;
  }

  await getActiveTabInfo();
  const message = (messageOverride || el('goalInput').value || '').trim();
  if (!message) {
    showAlert('Please enter a goal or question.', 'warn');
    return;
  }

  appendFeed('You', message, message);
  const domSnapshot = includeDom ? await captureActiveTabDom() : null;

  const step = await sendBg('AGENT_CHAT_STEP', {
    apiBaseUrl: getApiBaseUrl(),
    user_id: state.userId,
    profile_id: state.profileId || undefined,
    task_id: state.taskId,
    message,
    language: state.lang,
    current_url: state.activeUrl || null,
    dom_snapshot: domSnapshot,
  });
  await handleAgentResponse(step);
}

async function verifySubmissionReceipt() {
  await getActiveTabInfo();
  const [domSnapshot, domReceipt] = await Promise.all([
    captureActiveTabDom(),
    captureActiveTabReceipt(),
  ]);

  if (state.taskId && state.userId && domSnapshot) {
    const step = await sendBg('AGENT_CHAT_STEP', {
      apiBaseUrl: getApiBaseUrl(),
      user_id: state.userId,
      profile_id: state.profileId || undefined,
      task_id: state.taskId,
      message: 'Capture and verify submission receipt',
      language: state.lang,
      current_url: state.activeUrl,
      dom_snapshot: domSnapshot,
    });
    await handleAgentResponse(step);
    return;
  }

  if (domReceipt && domReceipt.receipt_number) {
    renderReceipt({ is_success: true, receipt_number: domReceipt.receipt_number }, 'submitted');
  } else {
    renderReceipt({ is_success: false, receipt_number: null }, 'unconfirmed');
  }
}

// ---------------------------------------------------------------------------
// View 3: Document Upload, Extraction, Mapped Fields & FR-037 Verification
// ---------------------------------------------------------------------------

function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = String(reader.result || '');
      const comma = result.indexOf(',');
      resolve(comma !== -1 ? result.slice(comma + 1) : result);
    };
    reader.onerror = () => reject(reader.error || new Error('Failed to read file.'));
    reader.readAsDataURL(file);
  });
}

async function previewMappedFormFields(docType, availableFieldKeys) {
  const box = el('mappedFieldsPreview');
  box.classList.add('hidden');
  box.innerHTML = '';

  await getActiveTabInfo();
  const domSnapshot = await captureActiveTabDom();
  if (!domSnapshot || !Array.isArray(domSnapshot.fields) || domSnapshot.fields.length === 0) {
    return;
  }

  try {
    const resp = await fetch(`${getApiBaseUrl()}/documents/map-fields`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        profile_id: state.profileSlug || 'self',
        doc_type: docType,
        available_field_keys: availableFieldKeys,
        form_fields: domSnapshot.fields.map((f) => ({
          element_key: f.element_key,
          label: f.label || f.name || f.id || f.element_key,
          input_type: f.input_type || 'text',
          required: Boolean(f.is_required || f.aria_required),
        })),
      }),
    });
    if (!resp.ok) return;
    const mapped = await resp.json();
    const mappings = Array.isArray(mapped.mappings) ? mapped.mappings : [];
    if (mappings.length === 0) return;

    box.classList.remove('hidden');
    const header = document.createElement('div');
    header.className = 'field-label';
    header.textContent =
      state.lang === 'hi'
        ? 'सक्रिय पेज पर मैप किए गए फ़ॉर्म फ़ील्ड'
        : 'Mapped Form Fields on Active Page';
    box.appendChild(header);

    mappings.forEach((m) => {
      const row = document.createElement('div');
      row.className = 'plan-step';
      const pct = Math.round((m.confidence || 0) * 100);
      row.innerHTML = `<span>${m.target_label}</span><span class="mono">${m.source_ref} (${pct}%)</span>`;
      box.appendChild(row);
    });
  } catch (_err) {
    // Non-fatal preview
  }
}

function renderExtractionResult(result) {
  state.lastExtraction = result;
  const v = result.validation || {};
  const badge = el('validationBadge');
  badge.textContent = v.overall_passed
    ? `Passed (${result.detected_doc_type})`
    : `Review Required (${result.detected_doc_type})`;
  badge.className = v.overall_passed ? 'badge ok' : 'badge warn';

  const summary = el('stageSummary');
  summary.classList.remove('hidden');
  const stages = [
    ['1. Technical Specs', v.stage_1_technical_specs],
    ['2. Visual Quality', v.stage_2_visual_quality],
    ['3. Classification', v.stage_3_document_classification],
    ['4. Cross-Field', v.stage_4_cross_field_consistency],
  ];
  summary.innerHTML = stages
    .map(([label, st]) => {
      const ok = st && st.passed;
      const errText =
        st && Array.isArray(st.errors) && st.errors.length ? ` (${st.errors.join('; ')})` : '';
      return `<div><span class="badge ${ok ? 'ok' : 'err'}">${ok ? 'PASS' : 'FAIL'}</span> ${label}${errText}</div>`;
    })
    .join('');

  if (v.auto_formatted_applied && v.auto_formatted_summary) {
    const note = document.createElement('div');
    note.className = 'muted';
    note.textContent = `Auto-format: ${v.auto_formatted_summary}`;
    summary.appendChild(note);
  }

  const list = el('extractedFieldsList');
  list.innerHTML = '';
  const fields = Array.isArray(result.fields) ? result.fields : [];
  fields.forEach((f, idx) => {
    const row = document.createElement('div');
    row.className = 'field-item';

    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.id = `verify_cb_${idx}`;
    cb.dataset.fieldKey = f.key;
    // Strict FR-037: require explicit user check before marking verifiedByUser === true.
    cb.checked = false;

    const meta = document.createElement('div');
    meta.className = 'field-meta';
    const lbl = document.createElement('label');
    lbl.className = 'field-key';
    lbl.setAttribute('for', cb.id);

    const confPct = Math.round((f.confidence || 0) * 100);
    const confBadgeClass = confPct >= 85 ? 'ok' : confPct >= 65 ? 'warn' : 'err';
    lbl.innerHTML = `<span>${f.key}</span><span class="badge ${confBadgeClass}">${confPct}% confidence</span>`;

    const val = document.createElement('div');
    val.className = 'field-val';
    val.textContent = f.value;

    meta.appendChild(lbl);
    meta.appendChild(val);
    row.appendChild(cb);
    row.appendChild(meta);
    list.appendChild(row);
  });

  if (fields.length > 0) {
    el('commitVaultControls').classList.remove('hidden');
    previewMappedFormFields(
      result.detected_doc_type,
      fields.map((f) => f.key)
    );
  } else {
    el('commitVaultControls').classList.add('hidden');
  }
}

// ---------------------------------------------------------------------------
// Event Wiring
// ---------------------------------------------------------------------------

document.addEventListener('DOMContentLoaded', async () => {
  applyLanguage('en');

  el('langEnBtn').addEventListener('click', () => applyLanguage('en'));
  el('langHiBtn').addEventListener('click', () => applyLanguage('hi'));

  el('apiBaseUrlInput').addEventListener('change', () => {
    state.apiBaseUrl = getApiBaseUrl();
    refreshDomainShield();
  });

  el('refreshShieldBtn').addEventListener('click', () => {
    refreshDomainShield();
  });

  el('authInitBtn').addEventListener('click', () => {
    handleConnectDevLogin().catch((err) => showAlert(err.message, 'err'));
  });

  el('unlockVaultBtn').addEventListener('click', async () => {
    clearAlert();
    try {
      const passphrase = el('vaultPassphraseInput').value;
      if (!passphrase) {
        showAlert('Enter a vault passphrase to unlock.', 'warn');
        return;
      }
      const res = await sendBg('SET_VAULT_PASSPHRASE', { passphrase });
      el('vaultPassphraseInput').value = '';
      state.vaultUnlocked = Boolean(res.unlocked);
      el('vaultLockBadge').textContent = state.vaultUnlocked ? 'Unlocked' : 'Locked';
      el('vaultLockBadge').className = state.vaultUnlocked ? 'badge ok' : 'badge warn';
      await refreshVaultList(passphrase);
      showAlert('Extension CryptoVault unlocked in chrome.storage.session.', 'ok');
    } catch (err) {
      showAlert(err.message, 'err');
    }
  });

  el('lockVaultBtn').addEventListener('click', async () => {
    clearAlert();
    try {
      await sendBg('SET_VAULT_PASSPHRASE', { passphrase: '' });
      state.vaultUnlocked = false;
      el('vaultLockBadge').textContent = 'Locked';
      el('vaultLockBadge').className = 'badge warn';
      showAlert('CryptoVault locked and session passphrase cleared.', 'warn');
    } catch (err) {
      showAlert(err.message, 'err');
    }
  });

  el('sendGoalBtn').addEventListener('click', () => {
    runAgentTurn().catch((err) => showAlert(err.message, 'err'));
  });

  el('inspectGuideBtn').addEventListener('click', () => {
    const query = el('goalInput').value.trim() || 'Where is my account number on the passbook?';
    runAgentTurn(query, { includeDom: false }).catch((err) => showAlert(err.message, 'err'));
  });

  el('sendClarificationBtn').addEventListener('click', () => {
    const answer = el('clarificationInput').value.trim();
    if (!answer) {
      showAlert('Enter your clarification answer first.', 'warn');
      return;
    }
    el('clarificationInput').value = '';
    runAgentTurn(answer).catch((err) => showAlert(err.message, 'err'));
  });

  el('reviewPendingBtn').addEventListener('click', () => {
    if (state.pendingApproval) {
      showActionBoundReview(state.pendingApproval);
    }
  });

  el('extractDocBtn').addEventListener('click', async () => {
    clearAlert();
    try {
      const fileInput = el('docFileInput');
      const file = fileInput.files && fileInput.files[0];
      if (!file) {
        showAlert('Choose an image or PDF document first.', 'warn');
        return;
      }
      const fileBase64 = await fileToBase64(file);
      state.lastRawFileBase64 = fileBase64;
      state.lastMimeType = file.type || 'application/octet-stream';

      const extraction = await sendBg('UPLOAD_AND_EXTRACT_DOC', {
        apiBaseUrl: getApiBaseUrl(),
        fileBase64,
        fileName: file.name,
        mimeType: state.lastMimeType,
        expected_doc_type: el('docTypeSelect').value,
        profile_id: state.profileSlug || 'self',
        profile_full_name: (el('userFullNameInput').value || '').trim() || undefined,
        language: state.lang,
      });

      renderExtractionResult(extraction);
      showAlert('4-stage validation complete. Check verifiedByUser boxes before saving to CryptoVault.', 'ok');
    } catch (err) {
      showAlert(err.message, 'err');
    }
  });

  el('commitVaultBtn').addEventListener('click', async () => {
    clearAlert();
    try {
      if (!state.lastExtraction) return;

      const extractedFields = {};
      const fields = state.lastExtraction.fields || [];
      fields.forEach((f, idx) => {
        const cb = el(`verify_cb_${idx}`);
        if (cb && cb.checked) {
          extractedFields[f.key] = {
            key: f.key,
            value: f.value,
            confidence: f.confidence,
            verifiedByUser: true,
          };
        }
      });

      if (Object.keys(extractedFields).length === 0) {
        showAlert('Check at least one extracted field (verifiedByUser === true) before saving (FR-037).', 'warn');
        return;
      }

      const inlinePassphrase = (el('docVaultPassphraseInput').value || '').trim();
      if (!state.vaultUnlocked && !inlinePassphrase) {
        showAlert('Enter or unlock your CryptoVault passphrase first.', 'warn');
        return;
      }

      if (inlinePassphrase) {
        await sendBg('SET_VAULT_PASSPHRASE', { passphrase: inlinePassphrase });
        el('docVaultPassphraseInput').value = '';
        state.vaultUnlocked = true;
        el('vaultLockBadge').textContent = 'Unlocked';
        el('vaultLockBadge').className = 'badge ok';
      }

      const v = state.lastExtraction.validation || {};
      const rawBase64 = v.formatted_file_base64 || state.lastRawFileBase64;
      const mimeType = v.formatted_mime_type || state.lastMimeType;

      await sendBg('SAVE_VERIFIED_DOC', {
        profileId: state.profileSlug || 'self',
        profileDisplayName: (el('userFullNameInput').value || '').trim() || 'Self',
        docType: state.lastExtraction.detected_doc_type,
        source: 'upload',
        extractedFields,
        mimeType,
        rawFileBase64: rawBase64,
        passphrase: inlinePassphrase || undefined,
      });

      await refreshVaultList(inlinePassphrase || undefined);
      showAlert('Verified fields encrypted (AES-256-GCM) and saved to CryptoVault.', 'ok');
    } catch (err) {
      showAlert(err.message, 'err');
    }
  });

  el('verifyReceiptBtn').addEventListener('click', () => {
    clearAlert();
    verifySubmissionReceipt().catch((err) => showAlert(err.message, 'err'));
  });

  el('inlineApproveBtn').addEventListener('click', () => {
    submitApprovalDecision(true).catch((err) => showAlert(err.message, 'err'));
  });
  el('inlineRejectBtn').addEventListener('click', () => {
    submitApprovalDecision(false).catch((err) => showAlert(err.message, 'err'));
  });
  el('approvalConfirmBtn').addEventListener('click', () => {
    submitApprovalDecision(true).catch((err) => showAlert(err.message, 'err'));
  });
  el('approvalRejectBtn').addEventListener('click', () => {
    submitApprovalDecision(false).catch((err) => showAlert(err.message, 'err'));
  });

  await Promise.all([refreshDomainShield(), refreshVaultList()]);
});
