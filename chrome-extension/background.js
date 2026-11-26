/**
 * MV3 Background Service Worker for Universal AI Web Assistant.
 *
 * Security Boundary:
 * 1. Loads compiled `dist/vault/cryptoVault.js` via `importScripts` inside the
 *    `chrome-extension://<id>` origin so IndexedDB (`ua_local_vault_db`) and
 *    `chrome.storage.session` never execute in untrusted webpage origins.
 * 2. Stores `authToken`, `approvedTaskHashes`, and session passphrase strictly in
 *    `chrome.storage.session` + SW state.
 * 3. Resolves `vault:<profileId>.<docType>.<fieldKey>` tokens ONLY via
 *    `RESOLVE_VAULT_TOKENS` when `msg.task_id` has an active entry in
 *    `approvedTaskHashes` and enforces FR-037 `verifiedByUser === true`.
 */

/* global importScripts, CryptoVault */
'use strict';

importScripts('dist/vault/cryptoVault.js');

const DEFAULT_API_BASE_URL = 'http://127.0.0.1:8000';
const SESSION_AUTH_TOKEN_KEY = 'ua_auth_token';
const SESSION_PASSPHRASE_KEY = 'ua_vault_passphrase';
const SESSION_APPROVED_HASHES_KEY = 'ua_approved_task_hashes';
const SESSION_AUTHORIZED_FILL_KEY = 'ua_authorized_fill_tasks';
const SESSION_STATE_META_KEY = 'ua_session_meta';

const swState = {
  apiBaseUrl: DEFAULT_API_BASE_URL,
  authToken: null,
  userId: null,
  profileId: null,
  approvedTaskHashes: {},
  authorizedFillTasks: {},
};

// Keep Side Panel available on action/command triggers when configured.
if (chrome.sidePanel && typeof chrome.sidePanel.setPanelBehavior === 'function') {
  chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => {});
}

// ---------------------------------------------------------------------------
// Session State Helpers (`chrome.storage.session` + in-memory cache)
// ---------------------------------------------------------------------------

async function sessionGet(keys) {
  if (chrome.storage && chrome.storage.session && typeof chrome.storage.session.get === 'function') {
    return chrome.storage.session.get(keys);
  }
  return {};
}

async function sessionSet(values) {
  if (chrome.storage && chrome.storage.session && typeof chrome.storage.session.set === 'function') {
    await chrome.storage.session.set(values);
  }
}

async function sessionRemove(keys) {
  if (chrome.storage && chrome.storage.session && typeof chrome.storage.session.remove === 'function') {
    await chrome.storage.session.remove(keys);
  }
}

async function hydrateSessionState() {
  const sess = await sessionGet([
    SESSION_AUTH_TOKEN_KEY,
    SESSION_APPROVED_HASHES_KEY,
    SESSION_AUTHORIZED_FILL_KEY,
    SESSION_STATE_META_KEY,
  ]);
  if (typeof sess[SESSION_AUTH_TOKEN_KEY] === 'string' && sess[SESSION_AUTH_TOKEN_KEY]) {
    swState.authToken = sess[SESSION_AUTH_TOKEN_KEY];
  }
  if (sess[SESSION_APPROVED_HASHES_KEY] && typeof sess[SESSION_APPROVED_HASHES_KEY] === 'object') {
    swState.approvedTaskHashes = {
      ...sess[SESSION_APPROVED_HASHES_KEY],
      ...swState.approvedTaskHashes,
    };
  }
  if (sess[SESSION_AUTHORIZED_FILL_KEY] && typeof sess[SESSION_AUTHORIZED_FILL_KEY] === 'object') {
    swState.authorizedFillTasks = {
      ...sess[SESSION_AUTHORIZED_FILL_KEY],
      ...swState.authorizedFillTasks,
    };
  }
  const meta = sess[SESSION_STATE_META_KEY];
  if (meta && typeof meta === 'object') {
    if (!swState.apiBaseUrl && meta.apiBaseUrl) swState.apiBaseUrl = meta.apiBaseUrl;
    if (meta.apiBaseUrl) swState.apiBaseUrl = String(meta.apiBaseUrl).replace(/\/+$/, '');
    if (!swState.userId && meta.userId) swState.userId = meta.userId;
    if (!swState.profileId && meta.profileId) swState.profileId = meta.profileId;
  }
  return swState;
}

async function persistSessionMeta() {
  await sessionSet({
    [SESSION_STATE_META_KEY]: {
      apiBaseUrl: swState.apiBaseUrl,
      userId: swState.userId,
      profileId: swState.profileId,
    },
  });
}

async function persistApprovedTaskHash(taskId, actionHash) {
  const key = String(taskId);
  swState.approvedTaskHashes[key] = String(actionHash);
  await sessionSet({ [SESSION_APPROVED_HASHES_KEY]: swState.approvedTaskHashes });
}

async function persistAuthorizedFillTask(taskId) {
  const key = String(taskId);
  swState.authorizedFillTasks[key] = true;
  await sessionSet({ [SESSION_AUTHORIZED_FILL_KEY]: swState.authorizedFillTasks });
}

async function resolvePassphrase(msg) {
  if (msg && typeof msg.passphrase === 'string' && msg.passphrase.length > 0) {
    return msg.passphrase;
  }
  const sess = await sessionGet([SESSION_PASSPHRASE_KEY]);
  if (typeof sess[SESSION_PASSPHRASE_KEY] === 'string' && sess[SESSION_PASSPHRASE_KEY].length > 0) {
    return sess[SESSION_PASSPHRASE_KEY];
  }
  return null;
}

// ---------------------------------------------------------------------------
// Backend HTTP Client Helpers
// ---------------------------------------------------------------------------

async function apiFetch(path, { method = 'GET', jsonBody = undefined, formData = undefined, baseUrl = undefined } = {}) {
  await hydrateSessionState();
  const root = String(baseUrl || swState.apiBaseUrl || DEFAULT_API_BASE_URL).replace(/\/+$/, '');
  const headers = {};
  if (swState.authToken) {
    headers['Authorization'] = `Bearer ${swState.authToken}`;
  }

  const init = { method, headers };
  if (jsonBody !== undefined) {
    headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(jsonBody);
  } else if (formData !== undefined) {
    init.body = formData;
  }

  const resp = await fetch(`${root}${path}`, init);
  const ct = resp.headers.get('content-type') || '';
  const data = ct.includes('application/json') ? await resp.json() : await resp.text();
  if (!resp.ok) {
    const detail = (data && typeof data === 'object' && (data.detail || data.message)) || String(data);
    throw new Error(`HTTP ${resp.status} on ${method} ${path}: ${detail}`);
  }
  return data;
}

function toUint8Array(payload) {
  if (payload instanceof Uint8Array) {
    return payload;
  }
  if (payload instanceof ArrayBuffer) {
    return new Uint8Array(payload);
  }
  if (Array.isArray(payload)) {
    return new Uint8Array(payload);
  }
  if (typeof payload === 'string') {
    const clean = payload.includes(',') ? payload.slice(payload.indexOf(',') + 1) : payload;
    const bin = atob(clean);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i += 1) {
      bytes[i] = bin.charCodeAt(i);
    }
    return bytes;
  }
  return new Uint8Array(0);
}

function maskFieldValue(value) {
  const str = String(value || '');
  if (!str) return '';
  if (str.length <= 4) return '****';
  return `${'*'.repeat(Math.max(4, str.length - 4))}${str.slice(-4)}`;
}

// ---------------------------------------------------------------------------
// Message Handlers
// ---------------------------------------------------------------------------

async function handleAuthDevLogin(msg) {
  await hydrateSessionState();
  if (msg.apiBaseUrl) {
    swState.apiBaseUrl = String(msg.apiBaseUrl).replace(/\/+$/, '');
  }
  const userId = String(msg.user_id || swState.userId || '').trim();
  if (!userId) {
    throw new Error('user_id is required for AUTH_DEV_LOGIN.');
  }

  const data = await apiFetch('/api/v1/auth/dev-token', {
    method: 'POST',
    jsonBody: {
      user_id: userId,
      expires_in_seconds: typeof msg.expires_in_seconds === 'number' ? msg.expires_in_seconds : 3600,
    },
    baseUrl: msg.apiBaseUrl,
  });

  if (!data || !data.access_token) {
    throw new Error('Backend dev-token endpoint did not return access_token.');
  }

  swState.authToken = data.access_token;
  swState.userId = userId;
  if (msg.profile_id) {
    swState.profileId = String(msg.profile_id);
  }

  await sessionSet({ [SESSION_AUTH_TOKEN_KEY]: data.access_token });
  await persistSessionMeta();
  return { ok: true, user_id: userId };
}

async function handleVerifyPortal(msg) {
  const targetUrl = String(msg.url || '').trim();
  if (!targetUrl) {
    throw new Error('url is required for VERIFY_PORTAL.');
  }
  const verdict = await apiFetch(`/api/v1/portals/verify?url=${encodeURIComponent(targetUrl)}`, {
    method: 'GET',
    baseUrl: msg.apiBaseUrl,
  });
  return { ok: true, ...verdict };
}

async function handleUploadAndExtractDoc(msg) {
  const bytes = toUint8Array(msg.fileBytes || msg.fileBase64 || msg.buffer || msg.bytes);
  if (!bytes || bytes.byteLength === 0) {
    throw new Error('Document fileBytes or fileBase64 payload is required.');
  }

  const mimeType = msg.mimeType || msg.mime_type || 'application/octet-stream';
  const fileName = msg.fileName || msg.file_name || 'document';
  const blob = new Blob([bytes], { type: mimeType });

  const form = new FormData();
  form.append('file', blob, fileName);
  form.append('expected_doc_type', msg.expected_doc_type || msg.expectedDocType || 'unknown');
  form.append('profile_id', String(msg.profile_id || msg.profileId || swState.profileId || 'self'));
  if (msg.profile_full_name || msg.profileFullName) {
    form.append('profile_full_name', msg.profile_full_name || msg.profileFullName);
  }
  form.append('language', msg.language || 'en');
  if (msg.portal_constraints_json || msg.portalConstraints) {
    const rawConstraints = msg.portal_constraints_json || JSON.stringify(msg.portalConstraints);
    form.append('portal_constraints_json', rawConstraints);
  }

  const extraction = await apiFetch('/documents/extract-and-validate', {
    method: 'POST',
    formData: form,
    baseUrl: msg.apiBaseUrl,
  });
  return { ok: true, ...extraction };
}

async function handleSaveVerifiedDoc(msg) {
  const vault = globalThis.CryptoVault;
  if (!vault || typeof vault.getLocalVault !== 'function') {
    throw new Error('CryptoVault runtime is unavailable in service worker.');
  }

  const passphrase = await resolvePassphrase(msg);
  if (!passphrase) {
    throw new Error('Vault passphrase is required to encrypt and save verified document.');
  }

  const incomingRecord = msg.record && typeof msg.record === 'object' ? msg.record : {};
  const rawFields = msg.extractedFields || incomingRecord.extractedFields || {};
  const fieldEntries = Object.entries(rawFields);
  if (fieldEntries.length === 0) {
    throw new Error('Cannot save document with zero extractedFields (FR-037).');
  }

  // Enforce PRD FR-037: every saved field must carry verifiedByUser === true.
  const validatedFields = {};
  for (const [key, field] of fieldEntries) {
    if (!field || field.verifiedByUser !== true) {
      throw new Error(`Field '${key}' must have verifiedByUser === true before CryptoVault commit (FR-037).`);
    }
    validatedFields[key] = {
      key: field.key || key,
      value: String(field.value ?? ''),
      confidence: typeof field.confidence === 'number' ? field.confidence : 1.0,
      verifiedByUser: true,
    };
  }

  const now = new Date().toISOString();
  const docId = incomingRecord.recordId || msg.docId || msg.recordId || crypto.randomUUID();
  const profileId = String(incomingRecord.profileId || msg.profileId || msg.profile_id || swState.profileId || 'self');
  const record = {
    recordId: docId,
    profileId,
    profileDisplayName: incomingRecord.profileDisplayName || msg.profileDisplayName || 'Self',
    docType: incomingRecord.docType || msg.docType || msg.doc_type || 'unknown',
    source: incomingRecord.source || msg.source || 'upload',
    extractedFields: validatedFields,
    encryptedBlobKey: incomingRecord.encryptedBlobKey || `blob_${docId}`,
    mimeType: incomingRecord.mimeType || msg.mimeType || 'application/octet-stream',
    createdAt: incomingRecord.createdAt || now,
    updatedAt: now,
  };

  const filePayload = msg.rawFileBase64 || msg.fileBytes || msg.fileBase64 || '';
  await vault.getLocalVault().saveDocumentRecord(record, filePayload, passphrase);
  return { ok: true, docId };
}

async function handleListVaultDocs(msg) {
  const vault = globalThis.CryptoVault;
  if (!vault || typeof vault.getLocalVault !== 'function') {
    throw new Error('CryptoVault runtime is unavailable in service worker.');
  }

  const profileId = String(msg.profileId || msg.profile_id || swState.profileId || 'self');
  const localVault = vault.getLocalVault();
  const summaries = await localVault.listRecordsByProfile(profileId);
  const passphrase = await resolvePassphrase(msg);

  const docs = [];
  for (const summary of summaries) {
    const maskedFields = {};
    if (passphrase) {
      try {
        const entry = await localVault.getDocumentRecord(summary.recordId, passphrase);
        const extracted = (entry && entry.record && entry.record.extractedFields) || {};
        for (const [k, field] of Object.entries(extracted)) {
          maskedFields[k] = {
            key: field.key || k,
            maskedValue: maskFieldValue(field.value),
            confidence: field.confidence,
            verifiedByUser: Boolean(field.verifiedByUser),
          };
        }
      } catch (_err) {
        // Keep metadata summary if decryption passphrase does not match this row.
      }
    }
    docs.push({
      ...summary,
      docId: summary.recordId,
      maskedFields,
    });
  }

  return { ok: true, docs };
}

async function handleAgentChatStep(msg) {
  await hydrateSessionState();
  const userId = String(msg.user_id || swState.userId || '').trim();
  if (!userId) {
    throw new Error('user_id is required for AGENT_CHAT_STEP.');
  }
  const profileId = msg.profile_id || swState.profileId || null;
  if (profileId && !swState.profileId) {
    swState.profileId = String(profileId);
    await persistSessionMeta();
  }

  const body = {
    user_id: userId,
    profile_id: profileId,
    task_id: msg.task_id || null,
    message: msg.message || msg.goal || '',
    language: msg.language || 'en',
    current_url: msg.current_url || (msg.dom_snapshot && msg.dom_snapshot.url) || null,
    dom_snapshot: msg.dom_snapshot || null,
  };

  const step = await apiFetch('/api/v1/agent/chat', {
    method: 'POST',
    jsonBody: body,
    baseUrl: msg.apiBaseUrl,
  });

  let pendingActionHash =
    step &&
    step.approval_request &&
    step.approval_request.action_payload &&
    step.approval_request.action_payload.action_hash
      ? step.approval_request.action_payload.action_hash
      : null;

  if (!pendingActionHash && step && step.task_id && (step.status === 'awaiting_submit_approval' || step.status === 'awaiting_approval')) {
    try {
      const taskRecord = await apiFetch(`/api/v1/tasks/${encodeURIComponent(step.task_id)}`, {
        method: 'GET',
        baseUrl: msg.apiBaseUrl,
      });
      pendingActionHash = taskRecord.pending_action_hash || null;
    } catch (_err) {
      pendingActionHash = null;
    }
  }

  const clarification =
    step && step.response_type === 'clarification'
      ? step.tool_input || { question: step.message, question_hi: step.message_hi }
      : null;

  return {
    ok: true,
    ...step,
    task_id: step.task_id || msg.task_id || null,
    status: step.status,
    pending_action_hash: pendingActionHash,
    actions: step.client_actions || [],
    visual_guide: step.visual_guide || null,
    clarification,
  };
}

async function getTargetTabId(msg) {
  if (typeof msg.tabId === 'number') {
    return msg.tabId;
  }
  const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  return tabs && tabs[0] && typeof tabs[0].id === 'number' ? tabs[0].id : null;
}

async function handleApproveTask(msg) {
  const taskId = String(msg.task_id || '').trim();
  if (!taskId) {
    throw new Error('task_id is required for APPROVE_TASK.');
  }
  if (!msg.action_hash || typeof msg.action_hash !== 'string') {
    throw new Error('action_hash is required for APPROVE_TASK.');
  }

  const approvalResp = await apiFetch(`/api/v1/tasks/${encodeURIComponent(taskId)}/approval`, {
    method: 'POST',
    jsonBody: {
      approved: Boolean(msg.approved),
      action_hash: msg.action_hash,
      reviewer_note: msg.reviewer_note || null,
    },
    baseUrl: msg.apiBaseUrl,
  });

  let tabResponse = null;
  if (msg.approved) {
    await persistApprovedTaskHash(taskId, msg.action_hash);
    const tabId = await getTargetTabId(msg);
    if (tabId !== null) {
      tabResponse = await chrome.tabs.sendMessage(tabId, {
        action: 'EXECUTE_APPROVED_ACTIONS',
        task_id: taskId,
        action_hash: msg.action_hash,
        actions: Array.isArray(msg.actions) ? msg.actions : [],
      });
    }
  }

  return { ok: true, approval: approvalResp, tab_response: tabResponse };
}

function formatSafePreviewValue(canonicalToken, value) {
  const str = String(value ?? '');
  if (!str) return '';
  const lowerToken = String(canonicalToken || '').toLowerCase();
  if (lowerToken.includes('aadhaar')) {
    return '[Aadhaar Redacted]';
  }
  if (lowerToken.includes('.pan.') || lowerToken.endsWith('pan_number')) {
    if (str.length >= 6) {
      return `${str.slice(0, 5)}****${str.slice(-1)}`;
    }
    return maskFieldValue(str);
  }
  if (lowerToken.includes('account_number')) {
    return maskFieldValue(str);
  }
  return str;
}

async function handlePreviewVaultFields(msg) {
  await hydrateSessionState();
  const vault = globalThis.CryptoVault;
  if (!vault || typeof vault.resolveVaultToken !== 'function' || typeof vault.standardizeVaultToken !== 'function') {
    throw new Error('CryptoVault runtime is unavailable in service worker.');
  }

  const passphrase = await resolvePassphrase(msg);
  if (!passphrase) {
    return { ok: true, previews: {}, locked: true };
  }

  const tokens = Array.isArray(msg.tokens) ? msg.tokens : [];
  const previews = {};
  for (const rawToken of tokens) {
    if (typeof rawToken !== 'string' || !rawToken.startsWith('vault:')) {
      continue;
    }
    try {
      const canonical = vault.standardizeVaultToken(rawToken);
      const resolved = await resolveWithFallback(vault, rawToken, passphrase);
      if (resolved !== null && resolved !== undefined) {
        const preview = formatSafePreviewValue(canonical, resolved);
        previews[rawToken] = preview;
        previews[canonical] = preview;
      }
    } catch (err) {
      previews[rawToken] = `[Locked / Unverified: ${err && err.message ? err.message : 'FR-037'}]`;
    }
  }

  return { ok: true, previews, locked: false };
}

function normalizeDateIso(rawValue) {
  const str = String(rawValue ?? '').trim();
  if (/^\d{4}-\d{2}-\d{2}$/.test(str)) {
    return str;
  }
  const ymd = str.match(/^(\d{4})[/\-.](\d{1,2})[/\-.](\d{1,2})$/);
  if (ymd) {
    return `${ymd[1]}-${ymd[2].padStart(2, '0')}-${ymd[3].padStart(2, '0')}`;
  }
  const dmy = str.match(/^(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})$/);
  if (dmy) {
    return `${dmy[3]}-${dmy[2].padStart(2, '0')}-${dmy[1].padStart(2, '0')}`;
  }
  return str;
}

function buildVaultFallbackCandidates(canonicalToken) {
  const candidates = [canonicalToken];
  if (canonicalToken.endsWith('.dob')) {
    candidates.push(canonicalToken.replace(/\.dob$/, '.date_of_birth'));
    if (canonicalToken.includes('.aadhaar.')) {
      candidates.push(canonicalToken.replace('.aadhaar.dob', '.pan.dob'));
      candidates.push(canonicalToken.replace('.aadhaar.dob', '.pan.date_of_birth'));
    } else if (canonicalToken.includes('.pan.')) {
      candidates.push(canonicalToken.replace('.pan.dob', '.aadhaar.dob'));
      candidates.push(canonicalToken.replace('.pan.dob', '.aadhaar.date_of_birth'));
    }
  } else if (canonicalToken.endsWith('.full_name')) {
    candidates.push(canonicalToken.replace(/\.full_name$/, '.name'));
    if (canonicalToken.includes('.aadhaar.')) {
      candidates.push(canonicalToken.replace('.aadhaar.full_name', '.pan.full_name'));
      candidates.push(canonicalToken.replace('.aadhaar.full_name', '.pan.name'));
    } else if (canonicalToken.includes('.pan.')) {
      candidates.push(canonicalToken.replace('.pan.full_name', '.aadhaar.full_name'));
      candidates.push(canonicalToken.replace('.pan.full_name', '.aadhaar.name'));
    }
  }
  return candidates;
}

async function resolveWithFallback(vault, rawToken, passphrase) {
  const canonical = vault.standardizeVaultToken(rawToken);
  const candidates = buildVaultFallbackCandidates(canonical);
  let lastError = null;
  for (const cand of candidates) {
    try {
      const val = await vault.resolveVaultToken(cand, {
        passphrase,
        allowUnverified: false,
      });
      if (val !== null && val !== undefined && String(val).length > 0) {
        if (canonical.endsWith('.dob')) {
          return normalizeDateIso(val);
        }
        return String(val);
      }
    } catch (err) {
      lastError = err;
    }
  }
  if (lastError) {
    throw lastError;
  }
  return null;
}

function isSensitivePreviewToken(token, flag) {
  if (flag) return true;
  const lower = String(token || '').toLowerCase();
  if (lower.endsWith('.dob') || lower.endsWith('.full_name') || lower.endsWith('.ifsc_code')) {
    return false;
  }
  return lower.includes('pan') || lower.includes('aadhaar') || lower.includes('account_number');
}

async function handlePreviewVaultActions(msg) {
  await hydrateSessionState();
  const vault = globalThis.CryptoVault;
  const actions = Array.isArray(msg.actions) ? msg.actions : [];
  const passphrase = await resolvePassphrase(msg);
  const items = [];

  for (let idx = 0; idx < actions.length; idx += 1) {
    const act = actions[idx];
    if (!act || act.action_type !== 'FILL_FIELD') {
      continue;
    }

    const actionId = String(act.element_key || act.selector || `field_${idx}`);
    const selector = String(act.selector || '');
    const rawDesc = String(act.description || '').trim();
    const label =
      act.field_label ||
      act.label ||
      (rawDesc.replace(/^(?:Fill(?:ing)?|Enter|Map(?:ped)?)\s+/i, '').trim() || selector || actionId);
    const ref = String(act.value_or_vault_ref || '');
    const sensitive = isSensitivePreviewToken(ref, Boolean(act.is_sensitive));

    let value = '';
    let maskedValue = '';
    let error = null;

    if (ref.startsWith('vault:')) {
      if (!passphrase) {
        error = 'Vault locked: unlock passphrase in Side Panel';
      } else if (!vault || typeof vault.resolveVaultToken !== 'function') {
        error = 'CryptoVault unavailable';
      } else {
        try {
          const canonical = vault.standardizeVaultToken(ref);
          const resolved = await resolveWithFallback(vault, ref, passphrase);
          if (resolved !== null && resolved !== undefined) {
            value = String(resolved);
            maskedValue = formatSafePreviewValue(canonical, resolved);
          } else {
            error = 'Not found in encrypted CryptoVault';
          }
        } catch (err) {
          error = err && err.message ? err.message : 'Verification required (FR-037)';
        }
      }
    } else {
      value = act.field_type === 'date' ? normalizeDateIso(ref) : ref;
      maskedValue = sensitive ? maskFieldValue(value) : value;
    }

    items.push({
      action_id: actionId,
      element_key: act.element_key || null,
      selector,
      label,
      token: ref,
      value,
      maskedValue: maskedValue || (sensitive ? '****' : value),
      isSensitive: sensitive,
      error,
    });
  }

  return { ok: true, items, locked: !passphrase };
}

async function handleAuthorizeFieldFill(msg) {
  await hydrateSessionState();
  const taskId = String(msg.task_id || '').trim();
  if (!taskId) {
    throw new Error('task_id is required for AUTHORIZE_FIELD_FILL.');
  }

  const actions = Array.isArray(msg.actions) ? msg.actions : [];
  if (actions.some((a) => a && a.action_type === 'CLICK_BUTTON')) {
    throw new Error('AUTHORIZE_FIELD_FILL cannot authorize CLICK_BUTTON or final submit; use APPROVE_TASK.');
  }

  await persistAuthorizedFillTask(taskId);

  let tabResponse = null;
  if (actions.length > 0 && msg.dispatch !== false) {
    const tabId = await getTargetTabId(msg);
    if (tabId !== null) {
      tabResponse = await chrome.tabs.sendMessage(tabId, {
        type: 'EXECUTE_APPROVED_ACTIONS',
        action: 'EXECUTE_APPROVED_ACTIONS',
        task_id: taskId,
        actions,
      });
    }
  }

  return { ok: true, task_id: taskId, authorized: true, tab_response: tabResponse };
}

async function handleResolveVaultTokens(msg) {
  await hydrateSessionState();
  const vault = globalThis.CryptoVault;
  if (!vault || typeof vault.resolveVaultToken !== 'function' || typeof vault.standardizeVaultToken !== 'function') {
    throw new Error('CryptoVault runtime is unavailable in service worker.');
  }

  // Reject unapproved or previewOnly attempts: raw vault values never resolve without authorization.
  if (msg.previewOnly === true) {
    throw new Error('previewOnly token resolution is blocked: raw vault values require explicit authorization.');
  }

  const taskId = String(msg.task_id || '').trim();
  const approvedHash = taskId ? swState.approvedTaskHashes[taskId] : null;
  const fillAuthorized = taskId ? Boolean(swState.authorizedFillTasks[taskId]) : false;

  if (!taskId || (!approvedHash && !fillAuthorized)) {
    throw new Error('Task has not been authorized for field fill or approved in approvedTaskHashes. Vault resolution denied.');
  }
  if (msg.action_type === 'CLICK_BUTTON' && !approvedHash) {
    throw new Error('CLICK_BUTTON / final submit requires explicit APPROVE_TASK with pending_action_hash.');
  }
  if (msg.action_hash && (!approvedHash || msg.action_hash !== approvedHash)) {
    throw new Error('action_hash mismatch against approvedTaskHashes.');
  }

  const passphrase = await resolvePassphrase(msg);
  if (!passphrase) {
    throw new Error('Vault passphrase is required in chrome.storage.session to decrypt vault tokens.');
  }

  const tokens = Array.isArray(msg.tokens) ? msg.tokens : [];
  const resolved = {};
  for (const rawToken of tokens) {
    if (typeof rawToken !== 'string' || !rawToken.startsWith('vault:')) {
      continue;
    }
    const canonical = vault.standardizeVaultToken(rawToken);
    // Strict FR-037 enforcement: allowUnverified is always false inside resolveWithFallback.
    const value = await resolveWithFallback(vault, rawToken, passphrase);
    if (value === null || value === undefined) {
      throw new Error(`Verified field not found in CryptoVault for token '${canonical}'.`);
    }
    resolved[rawToken] = value;
    resolved[canonical] = value;
  }

  return { ok: true, resolved };
}

// ---------------------------------------------------------------------------
// Extension Message Listener
// ---------------------------------------------------------------------------

function isTrustedExtensionPage(sender) {
  return Boolean(sender && sender.id === chrome.runtime.id && !sender.tab);
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (!msg || typeof msg.action !== 'string') {
    return false;
  }
  if (!sender || sender.id !== chrome.runtime.id) {
    sendResponse({ ok: false, error: 'Unauthorized message sender.' });
    return false;
  }

  (async () => {
    switch (msg.action) {
      case 'RESOLVE_VAULT_TOKENS':
        return handleResolveVaultTokens(msg);

      case 'SET_VAULT_PASSPHRASE': {
        if (!isTrustedExtensionPage(sender)) {
          throw new Error('Extension page origin required for SET_VAULT_PASSPHRASE.');
        }
        const passphrase = typeof msg.passphrase === 'string' ? msg.passphrase : '';
        if (!passphrase) {
          await sessionRemove([SESSION_PASSPHRASE_KEY]);
          return { ok: true, unlocked: false };
        }
        await sessionSet({ [SESSION_PASSPHRASE_KEY]: passphrase });
        return { ok: true, unlocked: true };
      }

      case 'AUTH_DEV_LOGIN':
        if (!isTrustedExtensionPage(sender)) {
          throw new Error('Extension page origin required for AUTH_DEV_LOGIN.');
        }
        return handleAuthDevLogin(msg);

      case 'VERIFY_PORTAL':
        return handleVerifyPortal(msg);

      case 'UPLOAD_AND_EXTRACT_DOC':
        if (!isTrustedExtensionPage(sender)) {
          throw new Error('Extension page origin required for UPLOAD_AND_EXTRACT_DOC.');
        }
        return handleUploadAndExtractDoc(msg);

      case 'SAVE_VERIFIED_DOC':
        if (!isTrustedExtensionPage(sender)) {
          throw new Error('Extension page origin required for SAVE_VERIFIED_DOC.');
        }
        return handleSaveVerifiedDoc(msg);

      case 'LIST_VAULT_DOCS':
        if (!isTrustedExtensionPage(sender)) {
          throw new Error('Extension page origin required for LIST_VAULT_DOCS.');
        }
        return handleListVaultDocs(msg);

      case 'AGENT_CHAT_STEP':
        if (!isTrustedExtensionPage(sender)) {
          throw new Error('Extension page origin required for AGENT_CHAT_STEP.');
        }
        return handleAgentChatStep(msg);

      case 'PREVIEW_VAULT_FIELDS':
        if (!isTrustedExtensionPage(sender)) {
          throw new Error('Extension page origin required for PREVIEW_VAULT_FIELDS.');
        }
        return handlePreviewVaultFields(msg);

      case 'PREVIEW_VAULT_ACTIONS':
        if (!isTrustedExtensionPage(sender)) {
          throw new Error('Extension page origin required for PREVIEW_VAULT_ACTIONS.');
        }
        return handlePreviewVaultActions(msg);

      case 'AUTHORIZE_FIELD_FILL':
        if (!isTrustedExtensionPage(sender)) {
          throw new Error('Extension page origin required for AUTHORIZE_FIELD_FILL.');
        }
        return handleAuthorizeFieldFill(msg);

      case 'APPROVE_TASK':
        if (!isTrustedExtensionPage(sender)) {
          throw new Error('Extension page origin required for APPROVE_TASK.');
        }
        return handleApproveTask(msg);

      case 'GET_TASK':
        if (!isTrustedExtensionPage(sender)) {
          throw new Error('Extension page origin required for GET_TASK.');
        }
        return apiFetch(`/api/v1/tasks/${encodeURIComponent(String(msg.task_id || '').trim())}`, {
          method: 'GET',
          baseUrl: msg.apiBaseUrl,
        });

      default:
        throw new Error(`Unknown action: ${msg.action}`);
    }
  })()
    .then((result) => sendResponse(result))
    .catch((err) => sendResponse({ ok: false, error: err && err.message ? err.message : String(err) }));

  return true;
});
