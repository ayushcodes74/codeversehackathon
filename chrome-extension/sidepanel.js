/**
 * Bilingual (EN / Hinglish) Side Panel Controller for Universal AI Web Assistant.
 *
 * All sensitive vault decryption and backend authenticated calls delegate to
 * `background.js` inside the `chrome-extension://<id>` security boundary.
 */

'use strict';

const I18N = {
  en: {
    appTitle: 'Universal AI Assistant',
    sessionVaultTitle: 'Session & Local Vault',
    apiUrlLabel: 'Backend API URL',
    fullNameLabel: 'Full Name',
    emailLabel: 'Email',
    signInBtn: 'Sign In / Init Profile',
    passphraseLabel: 'Local CryptoVault Passphrase',
    unlockBtn: 'Unlock',
    lockBtn: 'Lock',
    agentChatTitle: 'Goal Assistant & Visual Guide',
    chatEmpty: 'Ask a form question or submit your goal with the active portal page.',
    goalLabel: 'Goal or Question',
    sendGoalBtn: 'Run Agent on Page',
    visualGuideBtn: 'Visual Guide Q&A',
    reviewApprovalBtn: 'Review & Approve',
    docVaultTitle: 'Document Extraction & Vault Verification',
    docTypeLabel: 'Document Type',
    docFileLabel: 'Select Image or PDF',
    extractBtn: 'Extract & Validate (4-Stage)',
    commitVaultBtn: 'Commit Verified Fields to CryptoVault',
    receiptTitle: 'Submission Receipt Verification',
    receiptHint: 'After final submission, verify acknowledgement reference number here.',
    verifyReceiptBtn: 'Scan & Verify Receipt',
    approvalModalTitle: 'Action-Bound Review & Approval',
    targetDomainLabel: 'Target Domain',
    actionHashLabel: 'Action-Bound SHA-256 Hash (pending_action_hash)',
    mappedFieldsLabel: 'Mapped Fields / Actions',
    cancelBtn: 'Cancel',
    confirmApproveBtn: 'Approve & Execute',
  },
  hi: {
    appTitle: 'यूनिवर्सल AI वेब सहायक (EN / Hinglish)',
    sessionVaultTitle: 'सेशन और लोकल वॉल्ट (CryptoVault)',
    apiUrlLabel: 'बैकएंड API URL',
    fullNameLabel: 'पूरा नाम',
    emailLabel: 'ईमेल',
    signInBtn: 'साइन इन / प्रोफ़ाइल शुरू करें',
    passphraseLabel: 'लोकल वॉल्ट पासफ़्रेज़',
    unlockBtn: 'अनलॉक करें',
    lockBtn: 'लॉक करें',
    agentChatTitle: 'लक्ष्य सहायक और विज़ुअल गाइड',
    chatEmpty: 'फॉर्म के बारे में पूछें या वर्तमान पोर्टल पेज पर अपना लक्ष्य चलाएं।',
    goalLabel: 'लक्ष्य या सवाल',
    sendGoalBtn: 'पेज पर एजेंट चलाएं',
    visualGuideBtn: 'विज़ुअल गाइड Q&A',
    reviewApprovalBtn: 'समीक्षा और स्वीकृति दें',
    docVaultTitle: 'दस्तावेज़ सत्यापन और वॉल्ट (FR-037)',
    docTypeLabel: 'दस्तावेज़ प्रकार',
    docFileLabel: 'इमेज या PDF चुनें',
    extractBtn: 'एक्सट्रैक्ट और सत्यापित करें (4-Stage)',
    commitVaultBtn: 'सत्यापित फ़ील्ड वॉल्ट में सेव करें',
    receiptTitle: 'रसीद सत्यापन (Receipt Verification)',
    receiptHint: 'अंतिम सबमिट के बाद यहाँ रसीद संख्या सत्यापित करें।',
    verifyReceiptBtn: 'रसीद स्कैन और सत्यापित करें',
    approvalModalTitle: 'कार्रवाई-बद्ध समीक्षा और मानव स्वीकृति',
    targetDomainLabel: 'लक्ष्य डोमेन (Target Domain)',
    actionHashLabel: 'Action-Bound SHA-256 Hash (pending_action_hash)',
    mappedFieldsLabel: 'मैप किए गए फ़ील्ड / एक्शन',
    cancelBtn: 'रद्द करें',
    confirmApproveBtn: 'स्वीकृति दें और चलाएं',
  },
};

const state = {
  lang: 'en',
  activeTabId: null,
  activeUrl: '',
  activeDomain: '',
  taskId: null,
  lastExtraction: null,
  lastRawFileBase64: '',
  lastMimeType: '',
  pendingApproval: null,
};

function el(id) {
  return document.getElementById(id);
}

function sendBg(action, payload = {}) {
  return new Promise((resolve, reject) => {
    chrome.runtime.sendMessage({ action, ...payload }, (response) => {
      if (chrome.runtime.lastError) {
        reject(new Error(chrome.runtime.lastError.message));
        return;
      }
      if (!response || !response.success) {
        reject(new Error((response && response.error) || `Background action ${action} failed.`));
        return;
      }
      resolve(response.data);
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

async function refreshDomainShield() {
  const badge = el('domainShieldBadge');
  await getActiveTabInfo();
  if (!state.activeUrl || (!state.activeUrl.startsWith('http://') && !state.activeUrl.startsWith('https://'))) {
    badge.textContent = 'No Portal Tab';
    badge.className = 'badge warn';
    return;
  }
  try {
    const res = await sendBg('API_VERIFY_PORTAL', { url: state.activeUrl });
    if (res.is_official) {
      badge.textContent = `${res.support_tier}: ${res.canonical_domain}`;
      badge.className = 'badge ok';
    } else if (state.activeDomain === 'localhost' || state.activeDomain === '127.0.0.1') {
      badge.textContent = `local_dev: ${state.activeDomain}`;
      badge.className = 'badge ok';
    } else {
      badge.textContent = `blocked: ${res.canonical_domain || state.activeDomain}`;
      badge.className = 'badge err';
    }
  } catch (err) {
    badge.textContent = 'Domain Check Offline';
    badge.className = 'badge warn';
    console.warn('[SidePanel] Domain shield check failed:', err);
  }
}

async function refreshSessionState() {
  const sess = await sendBg('GET_SESSION_STATE');
  if (sess.settings) {
    el('apiBaseUrlInput').value = sess.settings.apiBaseUrl || 'http://127.0.0.1:8000';
    if (sess.settings.fullName) el('userFullNameInput').value = sess.settings.fullName;
    if (sess.settings.email) el('userEmailInput').value = sess.settings.email;
    applyLanguage(sess.settings.language || 'en');
  }

  const authBadge = el('authStatusBadge');
  if (sess.authenticated) {
    authBadge.textContent = `Signed In (${sess.settings.profileSlug || 'self'})`;
    authBadge.className = 'badge ok';
  } else {
    authBadge.textContent = 'Not Signed In';
    authBadge.className = 'badge warn';
  }

  const vaultBadge = el('vaultLockBadge');
  if (sess.vaultUnlocked) {
    vaultBadge.textContent = 'Unlocked';
    vaultBadge.className = 'badge ok';
    await refreshVaultRecords(sess.settings && sess.settings.profileSlug ? sess.settings.profileSlug : 'self');
  } else {
    vaultBadge.textContent = 'Locked';
    vaultBadge.className = 'badge warn';
  }
}

async function refreshVaultRecords(profileId) {
  try {
    const records = await sendBg('VAULT_LIST_RECORDS', { profileId });
    const node = el('vaultRecordsList');
    if (!records || records.length === 0) {
      node.textContent = state.lang === 'hi' ? 'वॉल्ट में कोई दस्तावेज़ नहीं है।' : 'No encrypted documents saved in extension vault yet.';
      return;
    }
    node.textContent =
      (state.lang === 'hi' ? 'वॉल्ट दस्तावेज़: ' : 'Encrypted Vault Docs: ') +
      records.map((r) => `${r.docType} (${r.profileId})`).join(', ');
  } catch (_err) {
    // Vault may still be initializing.
  }
}

function captureActiveTabDom() {
  return new Promise((resolve) => {
    if (!state.activeTabId) {
      resolve(null);
      return;
    }
    chrome.tabs.sendMessage(state.activeTabId, { action: 'INSPECT_DOM' }, (resp) => {
      if (chrome.runtime.lastError || !resp || !resp.success) {
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
    chrome.tabs.sendMessage(state.activeTabId, { action: 'CAPTURE_RECEIPT' }, (resp) => {
      if (chrome.runtime.lastError || !resp || !resp.success) {
        resolve(null);
        return;
      }
      resolve(resp.receipt || null);
    });
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
  el('vgExplanation').textContent = state.lang === 'hi' && card.explanation_hi ? card.explanation_hi : card.explanation;

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

function renderReceipt(receiptData, status) {
  const badge = el('receiptBadge');
  const body = el('receiptBody');
  const refNo = receiptData && (receiptData.receipt_number || (receiptData.captured_data && receiptData.captured_data.application_ref_no));
  const isConfirmed = Boolean(receiptData && receiptData.is_success && refNo);

  if (isConfirmed) {
    badge.textContent = 'Confirmed';
    badge.className = 'badge ok';
    body.className = 'status-banner show ok';
    body.textContent =
      state.lang === 'hi'
        ? `आवेदन सत्यापित रसीद संख्या: ${refNo} (Status: ${status || 'submitted'})`
        : `Confirmed Application Ref No: ${refNo} (Status: ${status || 'submitted'})`;
  } else {
    badge.textContent = 'Unconfirmed';
    badge.className = 'badge warn';
    body.className = 'status-banner show warn';
    body.textContent =
      state.lang === 'hi'
        ? 'चेतावनी: पृष्ठ पर कोई सत्यापित रसीद संदर्भ संख्या नहीं मिली। सबमिशन अपुष्ट (unconfirmed) है।'
        : 'Warning: No verified receipt reference number found on page. Submission remains unconfirmed (FR-061).';
  }
}

function openApprovalModal({ mode, taskId, targetDomain, actionHash, actions, summary }) {
  state.pendingApproval = { mode, taskId, targetDomain, actionHash, actions, summary };
  el('approvalTargetDomain').textContent = targetDomain || state.activeDomain || 'unknown';
  el('approvalActionHash').textContent = actionHash || '(local fill approval grant)';
  el('approvalModalSubtitle').textContent =
    mode === 'submit_approval'
      ? state.lang === 'hi'
        ? 'अंतिम पोर्टल सबमिट करने से पहले कृपया कार्रवाई-बद्ध हैश और विवरण सत्यापित करें।'
        : 'Final submit requires explicit human approval bound to pending_action_hash (FR-027).'
      : state.lang === 'hi'
        ? 'बैकग्राउंड CryptoVault टोकन डिक्रिप्ट करने से पहले मैप किए गए फ़ील्ड की पुष्टि करें।'
        : 'Confirm mapped fields before background CryptoVault resolves vault tokens for autofill.';

  const list = el('approvalMappedFields');
  list.innerHTML = '';
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
      list.appendChild(item);
    });
  } else if (summary && typeof summary === 'object') {
    Object.entries(summary).forEach(([k, v]) => {
      const item = document.createElement('div');
      item.className = 'field-item';
      item.textContent = `${k}: ${String(v)}`;
      list.appendChild(item);
    });
  }

  el('approvalModalBackdrop').classList.add('open');
  el('reviewPendingBtn').classList.remove('hidden');
}

function closeApprovalModal() {
  el('approvalModalBackdrop').classList.remove('open');
}

async function handleAgentResponse(step) {
  if (step.task_id) {
    state.taskId = step.task_id;
  }
  el('taskStateBadge').textContent = `${step.status} (${step.current_step_index}/${step.total_steps})`;
  appendFeed('Assistant', step.message, step.message_hi);

  if (step.visual_guide) {
    renderVisualGuide(step.visual_guide);
  }

  if (step.response_type === 'completed' || step.receipt_data) {
    renderReceipt(step.receipt_data, step.status);
  }

  if (step.response_type === 'approval_request' && step.approval_request) {
    let actionHash =
      step.approval_request.action_payload && step.approval_request.action_payload.action_hash;
    if (!actionHash && state.taskId) {
      const taskRecord = await sendBg('API_GET_TASK', { taskId: state.taskId });
      actionHash = taskRecord.pending_action_hash;
    }
    openApprovalModal({
      mode: 'submit_approval',
      taskId: state.taskId,
      targetDomain: state.activeDomain,
      actionHash,
      actions: [],
      summary: step.approval_request.summary_of_changes || step.approval_request.action_payload || {},
    });
    return;
  }

  if (step.response_type === 'action' && Array.isArray(step.client_actions) && step.client_actions.length > 0) {
    const requiresVaultResolution = step.client_actions.some(
      (a) => a && typeof a.value_or_vault_ref === 'string' && a.value_or_vault_ref.startsWith('vault:')
    );
    if (requiresVaultResolution || step.next_tool === 'fill_form_fields' || step.next_tool === 'submit_form') {
      let actionHash = null;
      if (state.taskId) {
        try {
          const taskRecord = await sendBg('API_GET_TASK', { taskId: state.taskId });
          actionHash = taskRecord.pending_action_hash;
        } catch (_err) {
          actionHash = null;
        }
      }
      openApprovalModal({
        mode: 'dispatch_actions',
        taskId: state.taskId,
        targetDomain: state.activeDomain,
        actionHash,
        actions: step.client_actions,
        summary: null,
      });
    }
  }
}

async function runAgentTurn(messageOverride = null) {
  clearAlert();
  await getActiveTabInfo();
  const message = (messageOverride || el('goalInput').value || '').trim();
  if (!message) {
    showAlert('Please enter a goal or question.', 'warn');
    return;
  }

  const sess = await sendBg('GET_SESSION_STATE');
  if (!sess.authenticated) {
    showAlert('Sign in / Init Profile before calling Agent Orchestrator.', 'warn');
    return;
  }

  appendFeed('You', message, message);
  const domSnapshot = messageOverride ? null : await captureActiveTabDom();

  const step = await sendBg('API_AGENT_CHAT', {
    payload: {
      task_id: state.taskId,
      message,
      language: state.lang,
      current_url: state.activeUrl || null,
      dom_snapshot: domSnapshot,
    },
  });
  await handleAgentResponse(step);
}

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

function renderExtractionResult(result) {
  state.lastExtraction = result;
  const v = result.validation || {};
  const badge = el('validationBadge');
  badge.textContent = v.overall_passed ? `Passed (${result.detected_doc_type})` : `Review Required (${result.detected_doc_type})`;
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
      const errText = st && Array.isArray(st.errors) && st.errors.length ? ` (${st.errors.join('; ')})` : '';
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
    // Strict FR-037: require explicit user check before marking verifiedByUser=true.
    cb.checked = false;

    const meta = document.createElement('div');
    meta.className = 'field-meta';
    const lbl = document.createElement('label');
    lbl.className = 'field-key';
    lbl.setAttribute('for', cb.id);
    lbl.textContent = `${f.key} (${Math.round((f.confidence || 0) * 100)}%)`;
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
    el('commitVaultBtn').classList.remove('hidden');
  } else {
    el('commitVaultBtn').classList.add('hidden');
  }
}

// ---------------------------------------------------------------------------
// Event Wiring
// ---------------------------------------------------------------------------

document.addEventListener('DOMContentLoaded', async () => {
  applyLanguage('en');

  el('langEnBtn').addEventListener('click', async () => {
    applyLanguage('en');
    await sendBg('SAVE_SETTINGS', { settings: { language: 'en' } }).catch(() => {});
  });
  el('langHiBtn').addEventListener('click', async () => {
    applyLanguage('hi');
    await sendBg('SAVE_SETTINGS', { settings: { language: 'hi' } }).catch(() => {});
  });

  el('apiBaseUrlInput').addEventListener('change', async () => {
    await sendBg('SAVE_SETTINGS', { settings: { apiBaseUrl: el('apiBaseUrlInput').value } });
    await refreshDomainShield();
  });

  el('authInitBtn').addEventListener('click', async () => {
    clearAlert();
    try {
      await sendBg('SAVE_SETTINGS', { settings: { apiBaseUrl: el('apiBaseUrlInput').value } });
      const fullName = el('userFullNameInput').value.trim() || 'Citizen User';
      const email = el('userEmailInput').value.trim() || `citizen_${Date.now()}@example.com`;
      await sendBg('API_USERS_AUTH', {
        payload: { full_name: fullName, email, language: state.lang },
      });
      await refreshSessionState();
      showAlert('Profile signed in and HS256 JWT stored in extension session.', 'ok');
    } catch (err) {
      showAlert(err.message, 'err');
    }
  });

  el('unlockVaultBtn').addEventListener('click', async () => {
    clearAlert();
    try {
      const passphrase = el('vaultPassphraseInput').value;
      await sendBg('VAULT_UNLOCK', { passphrase });
      el('vaultPassphraseInput').value = '';
      await refreshSessionState();
      showAlert('Extension-origin CryptoVault unlocked in chrome.storage.session.', 'ok');
    } catch (err) {
      showAlert(err.message, 'err');
    }
  });

  el('lockVaultBtn').addEventListener('click', async () => {
    await sendBg('VAULT_LOCK');
    await refreshSessionState();
    showAlert('CryptoVault locked and session passphrase cleared.', 'warn');
  });

  el('sendGoalBtn').addEventListener('click', () => {
    runAgentTurn().catch((err) => showAlert(err.message, 'err'));
  });

  el('inspectGuideBtn').addEventListener('click', () => {
    const query = el('goalInput').value.trim() || 'Where is my account number on the passbook?';
    runAgentTurn(query).catch((err) => showAlert(err.message, 'err'));
  });

  el('reviewPendingBtn').addEventListener('click', () => {
    if (state.pendingApproval) {
      openApprovalModal(state.pendingApproval);
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
      const sess = await sendBg('GET_SESSION_STATE');
      const fileBase64 = await fileToBase64(file);
      state.lastRawFileBase64 = fileBase64;
      state.lastMimeType = file.type || 'application/octet-stream';

      const extraction = await sendBg('API_EXTRACT_AND_VALIDATE', {
        payload: {
          fileBase64,
          fileName: file.name,
          mimeType: state.lastMimeType,
          expectedDocType: el('docTypeSelect').value,
          profileId: (sess.settings && sess.settings.profileSlug) || 'self',
          profileFullName: (sess.settings && sess.settings.fullName) || null,
          language: state.lang,
        },
      });
      renderExtractionResult(extraction);
      showAlert('4-stage validation complete. Check the fields you verify before committing to CryptoVault.', 'ok');
    } catch (err) {
      showAlert(err.message, 'err');
    }
  });

  el('commitVaultBtn').addEventListener('click', async () => {
    clearAlert();
    try {
      if (!state.lastExtraction) return;
      const sess = await sendBg('GET_SESSION_STATE');
      if (!sess.vaultUnlocked) {
        showAlert('Unlock your Local CryptoVault passphrase first.', 'warn');
        return;
      }

      const extractedFields = {};
      const fields = state.lastExtraction.fields || [];
      fields.forEach((f, idx) => {
        const cb = el(`verify_cb_${idx}`);
        extractedFields[f.key] = {
          key: f.key,
          value: f.value,
          confidence: f.confidence,
          verifiedByUser: Boolean(cb && cb.checked),
        };
      });

      const anyVerified = Object.values(extractedFields).some((f) => f.verifiedByUser);
      if (!anyVerified) {
        showAlert('Check at least one extracted field to mark verifiedByUser = true (FR-037).', 'warn');
        return;
      }

      const v = state.lastExtraction.validation || {};
      const rawBase64 = v.formatted_file_base64 || state.lastRawFileBase64;
      const mimeType = v.formatted_mime_type || state.lastMimeType;
      const profileSlug = (sess.settings && sess.settings.profileSlug) || 'self';

      await sendBg('VAULT_SAVE_RECORD', {
        payload: {
          profileId: profileSlug,
          profileDisplayName: (sess.settings && sess.settings.fullName) || 'Self',
          docType: state.lastExtraction.detected_doc_type,
          source: 'upload',
          extractedFields,
          mimeType,
          rawFileBase64: rawBase64,
        },
      });
      await refreshVaultRecords(profileSlug);
      showAlert('Verified fields encrypted (AES-256-GCM) and saved to extension-origin CryptoVault.', 'ok');
    } catch (err) {
      showAlert(err.message, 'err');
    }
  });

  el('verifyReceiptBtn').addEventListener('click', async () => {
    clearAlert();
    try {
      await getActiveTabInfo();
      const [domSnapshot, domReceipt] = await Promise.all([
        captureActiveTabDom(),
        captureActiveTabReceipt(),
      ]);

      if (state.taskId && domSnapshot) {
        const step = await sendBg('API_AGENT_CHAT', {
          payload: {
            task_id: state.taskId,
            message: 'Capture and verify submission receipt',
            language: state.lang,
            current_url: state.activeUrl,
            dom_snapshot: domSnapshot,
          },
        });
        await handleAgentResponse(step);
      } else if (domReceipt && domReceipt.receipt_number) {
        renderReceipt({ is_success: true, receipt_number: domReceipt.receipt_number }, 'submitted');
      } else {
        renderReceipt({ is_success: false, receipt_number: null }, 'unconfirmed');
      }
    } catch (err) {
      showAlert(err.message, 'err');
    }
  });

  el('approvalRejectBtn').addEventListener('click', async () => {
    const pending = state.pendingApproval;
    closeApprovalModal();
    if (pending && pending.mode === 'submit_approval' && pending.taskId && pending.actionHash) {
      try {
        await sendBg('API_APPROVE_TASK', {
          taskId: pending.taskId,
          payload: { approved: false, action_hash: pending.actionHash, reviewer_note: 'Cancelled in Side Panel' },
        });
        el('taskStateBadge').textContent = 'cancelled';
      } catch (err) {
        showAlert(err.message, 'err');
      }
    }
    state.pendingApproval = null;
    el('reviewPendingBtn').classList.add('hidden');
  });

  el('approvalConfirmBtn').addEventListener('click', async () => {
    const pending = state.pendingApproval;
    if (!pending) return;
    clearAlert();
    try {
      await getActiveTabInfo();
      if (pending.mode === 'submit_approval') {
        await sendBg('API_APPROVE_TASK', {
          taskId: pending.taskId,
          payload: {
            approved: true,
            action_hash: pending.actionHash,
            reviewer_note: 'Approved via Side Panel action-bound modal',
          },
        });
        closeApprovalModal();
        el('reviewPendingBtn').classList.add('hidden');
        state.pendingApproval = null;

        // Re-run agent turn on current DOM so verified CLICK_BUTTON submit action is dispatched.
        const domSnapshot = await captureActiveTabDom();
        const nextStep = await sendBg('API_AGENT_CHAT', {
          payload: {
            task_id: pending.taskId,
            message: 'Proceed with approved submission',
            language: state.lang,
            current_url: state.activeUrl,
            dom_snapshot: domSnapshot,
          },
        });
        await handleAgentResponse(nextStep);
        return;
      }

      if (pending.mode === 'dispatch_actions') {
        const result = await sendBg('DISPATCH_APPROVED_ACTIONS', {
          tabId: state.activeTabId,
          taskId: pending.taskId,
          actionHash: pending.actionHash,
          actions: pending.actions,
        });
        closeApprovalModal();
        el('reviewPendingBtn').classList.add('hidden');
        state.pendingApproval = null;
        const okCount = (result.tabResponse && result.tabResponse.results || []).filter((r) => r.success).length;
        showAlert(`Executed ${okCount} approved action(s) on ${pending.targetDomain || 'active page'}.`, 'ok');
      }
    } catch (err) {
      showAlert(err.message, 'err');
    }
  });

  await Promise.all([refreshSessionState(), refreshDomainShield()]);
});
