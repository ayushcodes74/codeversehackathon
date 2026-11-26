/**
 * MV3 Background Service Worker for Universal AI Web Assistant.
 *
 * Security Boundary:
 * 1. Hosts CryptoVault (`dist/vault/cryptoVault.js`) strictly in the extension origin
 *    (`chrome-extension://<id>`) so IndexedDB (`ua_local_vault_db`) and `chrome.storage.session`
 *    never live in untrusted portal origins.
 * 2. Resolves `vault:<profileId>.<docType>.<fieldKey>` tokens ONLY via `RESOLVE_VAULT_TOKENS`
 *    after an explicit user approval grant, enforcing FR-037 `verifiedByUser` verification.
 * 3. Provides backend API helpers for `/api/v1/users/auth`, `/api/v1/portals/verify`,
 *    `/documents/extract-and-validate`, `/api/v1/agent/chat`, and `/api/v1/tasks/{task_id}/approval`.
 */

/* global importScripts, CryptoVault */
'use strict';

importScripts('dist/vault/cryptoVault.js');

const DEFAULT_API_BASE_URL = 'http://127.0.0.1:8000';
const SESSION_PASSPHRASE_KEY = 'ua_vault_passphrase';
const SESSION_AUTH_TOKEN_KEY = 'ua_auth_token';
const SESSION_GRANTS_KEY = 'ua_approved_fill_grants';
const SETTINGS_STORAGE_KEY = 'ua_extension_settings';
const GRANT_TTL_MS = 10 * 60 * 1000;

let memorySessionFallback = {};

// Open Side Panel when the user clicks the extension toolbar icon.
if (chrome.sidePanel && typeof chrome.sidePanel.setPanelBehavior === 'function') {
  chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => {});
}

chrome.runtime.onInstalled.addListener(() => {
  if (chrome.sidePanel && typeof chrome.sidePanel.setPanelBehavior === 'function') {
    chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => {});
  }
});

// ---------------------------------------------------------------------------
// Storage Helpers (Extension-Origin Session & Settings)
// ---------------------------------------------------------------------------

async function getSessionValues(keys) {
  if (chrome.storage && chrome.storage.session) {
    return chrome.storage.session.get(keys);
  }
  const out = {};
  for (const k of keys) {
    if (Object.prototype.hasOwnProperty.call(memorySessionFallback, k)) {
      out[k] = memorySessionFallback[k];
    }
  }
  return out;
}

async function setSessionValues(values) {
  if (chrome.storage && chrome.storage.session) {
    await chrome.storage.session.set(values);
  } else {
    memorySessionFallback = { ...memorySessionFallback, ...values };
  }
}

async function removeSessionKeys(keys) {
  if (chrome.storage && chrome.storage.session) {
    await chrome.storage.session.remove(keys);
  } else {
    for (const k of keys) {
      delete memorySessionFallback[k];
    }
  }
}

async function getSettings() {
  const res = await chrome.storage.local.get([SETTINGS_STORAGE_KEY]);
  const stored = (res && res[SETTINGS_STORAGE_KEY]) || {};
  return {
    apiBaseUrl: (stored.apiBaseUrl || DEFAULT_API_BASE_URL).replace(/\/+$/, ''),
    userId: stored.userId || null,
    profileId: stored.profileId || null,
    profileSlug: stored.profileSlug || 'self',
    fullName: stored.fullName || '',
    email: stored.email || '',
    language: stored.language === 'hi' ? 'hi' : 'en',
  };
}

async function saveSettings(patch) {
  const current = await getSettings();
  const next = { ...current, ...patch };
  if (next.apiBaseUrl) {
    next.apiBaseUrl = next.apiBaseUrl.replace(/\/+$/, '');
  }
  await chrome.storage.local.set({ [SETTINGS_STORAGE_KEY]: next });
  return next;
}

async function getSessionState() {
  const [settings, sess] = await Promise.all([
    getSettings(),
    getSessionValues([SESSION_PASSPHRASE_KEY, SESSION_AUTH_TOKEN_KEY]),
  ]);
  return {
    settings,
    vaultUnlocked: Boolean(sess[SESSION_PASSPHRASE_KEY]),
    authenticated: Boolean(sess[SESSION_AUTH_TOKEN_KEY] && settings.userId),
  };
}

// ---------------------------------------------------------------------------
// Explicit Approval Grants & Extension-Origin CryptoVault Resolution
// ---------------------------------------------------------------------------

async function loadApprovalGrants() {
  const sess = await getSessionValues([SESSION_GRANTS_KEY]);
  const raw = sess[SESSION_GRANTS_KEY] || {};
  const now = Date.now();
  const active = {};
  for (const [grantId, grant] of Object.entries(raw)) {
    if (grant && typeof grant.expiresAt === 'number' && grant.expiresAt > now) {
      active[grantId] = grant;
    }
  }
  return active;
}

async function issueApprovalGrant({ tabId, taskId, actionHash, tokens }) {
  const vault = globalThis.CryptoVault;
  const canonicalTokens = (tokens || [])
    .filter((t) => typeof t === 'string' && t.startsWith('vault:'))
    .map((t) => (vault && typeof vault.standardizeVaultToken === 'function' ? vault.standardizeVaultToken(t) : t));

  const grantId = crypto.randomUUID();
  const grants = await loadApprovalGrants();
  grants[grantId] = {
    grantId,
    tabId: typeof tabId === 'number' ? tabId : null,
    taskId: taskId || null,
    actionHash: actionHash || null,
    tokens: Array.from(new Set(canonicalTokens)),
    approvedAt: Date.now(),
    expiresAt: Date.now() + GRANT_TTL_MS,
  };
  await setSessionValues({ [SESSION_GRANTS_KEY]: grants });
  return grants[grantId];
}

async function resolveApprovedVaultTokens({ tokens, approvalGrantId, senderTabId }) {
  const vault = globalThis.CryptoVault;
  if (!vault || typeof vault.resolveVaultToken !== 'function') {
    throw new Error('CryptoVault runtime is unavailable in service worker.');
  }
  if (!approvalGrantId || typeof approvalGrantId !== 'string') {
    throw new Error('Explicit user approval grant is required before resolving vault tokens.');
  }

  const grants = await loadApprovalGrants();
  const grant = grants[approvalGrantId];
  if (!grant) {
    throw new Error('Vault fill approval grant is missing or expired. Re-approve in Side Panel.');
  }
  if (grant.tabId !== null && typeof senderTabId === 'number' && grant.tabId !== senderTabId) {
    throw new Error('Approval grant tab mismatch: vault resolution denied.');
  }

  const sess = await getSessionValues([SESSION_PASSPHRASE_KEY]);
  const passphrase = sess[SESSION_PASSPHRASE_KEY];
  if (!passphrase) {
    throw new Error('CryptoVault is locked. Unlock your vault passphrase in the Side Panel.');
  }

  const resolved = {};
  for (const rawToken of tokens || []) {
    if (typeof rawToken !== 'string' || !rawToken.startsWith('vault:')) {
      continue;
    }
    const canonical =
      typeof vault.standardizeVaultToken === 'function'
        ? vault.standardizeVaultToken(rawToken)
        : rawToken;

    if (!grant.tokens.includes(canonical)) {
      throw new Error(`Token '${canonical}' was not covered by the approved action grant.`);
    }

    // Strict FR-037 enforcement: allowUnverified is always false during autofill resolution.
    const plaintext = await vault.resolveVaultToken(canonical, {
      passphrase,
      allowUnverified: false,
    });
    if (plaintext === null || plaintext === undefined) {
      throw new Error(`Verified field not found in CryptoVault for token '${canonical}'.`);
    }
    resolved[rawToken] = plaintext;
    resolved[canonical] = plaintext;
  }

  return resolved;
}

// ---------------------------------------------------------------------------
// Backend API Client Helpers
// ---------------------------------------------------------------------------

async function apiRequest(path, { method = 'GET', jsonBody = undefined, formData = undefined, requireAuth = false } = {}) {
  const settings = await getSettings();
  const sess = await getSessionValues([SESSION_AUTH_TOKEN_KEY]);
  const headers = {};
  if (sess[SESSION_AUTH_TOKEN_KEY]) {
    headers['Authorization'] = `Bearer ${sess[SESSION_AUTH_TOKEN_KEY]}`;
  } else if (requireAuth && settings.userId) {
    const tokenRes = await apiFetchDevToken(settings.userId);
    if (tokenRes && tokenRes.access_token) {
      headers['Authorization'] = `Bearer ${tokenRes.access_token}`;
    }
  }

  const init = { method, headers };
  if (jsonBody !== undefined) {
    headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(jsonBody);
  } else if (formData !== undefined) {
    init.body = formData;
  }

  const resp = await fetch(`${settings.apiBaseUrl}${path}`, init);
  const contentType = resp.headers.get('content-type') || '';
  const data = contentType.includes('application/json') ? await resp.json() : await resp.text();
  if (!resp.ok) {
    const detail = (data && typeof data === 'object' && (data.detail || data.message)) || String(data);
    throw new Error(`API ${method} ${path} failed (${resp.status}): ${detail}`);
  }
  return data;
}

async function apiFetchDevToken(userId) {
  const data = await apiRequest('/api/v1/auth/dev-token', {
    method: 'POST',
    jsonBody: { user_id: String(userId), expires_in_seconds: 3600 },
  });
  if (data && data.access_token) {
    await setSessionValues({ [SESSION_AUTH_TOKEN_KEY]: data.access_token });
  }
  return data;
}

async function apiUsersAuth(payload) {
  const authData = await apiRequest('/api/v1/users/auth', {
    method: 'POST',
    jsonBody: {
      google_sub: payload.google_sub || `ext_${crypto.randomUUID().slice(0, 12)}`,
      email: payload.email,
      full_name: payload.full_name,
      mobile_no: payload.mobile_no || null,
      preferred_language: payload.language || 'en',
    },
  });

  const defaultProfile = Array.isArray(authData.profiles) ? authData.profiles[0] : null;
  await saveSettings({
    userId: authData.user_id,
    profileId: defaultProfile ? defaultProfile.id : null,
    profileSlug: defaultProfile ? defaultProfile.profile_slug : 'self',
    fullName: payload.full_name,
    email: authData.email || payload.email,
    language: payload.language || 'en',
  });

  await apiFetchDevToken(authData.user_id);
  return authData;
}

async function apiVerifyPortal(urlOrDomain) {
  const encoded = encodeURIComponent(urlOrDomain);
  return apiRequest(`/api/v1/portals/verify?url=${encoded}`, { method: 'GET' });
}

function base64ToUint8Array(base64) {
  const clean = base64.includes(',') ? base64.slice(base64.indexOf(',') + 1) : base64;
  const bin = atob(clean);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i += 1) {
    bytes[i] = bin.charCodeAt(i);
  }
  return bytes;
}

async function apiExtractAndValidateDocument(payload) {
  const bytes = base64ToUint8Array(payload.fileBase64);
  const blob = new Blob([bytes], { type: payload.mimeType || 'application/octet-stream' });
  const form = new FormData();
  form.append('file', blob, payload.fileName || 'document');
  form.append('expected_doc_type', payload.expectedDocType || 'unknown');
  form.append('profile_id', payload.profileId || 'self');
  if (payload.profileFullName) {
    form.append('profile_full_name', payload.profileFullName);
  }
  form.append('language', payload.language || 'en');
  if (payload.portalConstraints) {
    form.append('portal_constraints_json', JSON.stringify(payload.portalConstraints));
  }

  return apiRequest('/documents/extract-and-validate', {
    method: 'POST',
    formData: form,
  });
}

async function apiAgentChat(payload) {
  const settings = await getSettings();
  const body = {
    user_id: payload.user_id || settings.userId,
    profile_id: payload.profile_id || settings.profileId,
    task_id: payload.task_id || null,
    message: payload.message,
    language: payload.language || settings.language || 'en',
    current_url: payload.current_url || null,
    dom_snapshot: payload.dom_snapshot || null,
  };
  return apiRequest('/api/v1/agent/chat', {
    method: 'POST',
    jsonBody: body,
    requireAuth: true,
  });
}

async function apiGetTask(taskId) {
  return apiRequest(`/api/v1/tasks/${encodeURIComponent(taskId)}`, {
    method: 'GET',
    requireAuth: true,
  });
}

async function apiApproveTask(taskId, payload) {
  return apiRequest(`/api/v1/tasks/${encodeURIComponent(taskId)}/approval`, {
    method: 'POST',
    jsonBody: {
      approved: Boolean(payload.approved),
      action_hash: payload.action_hash,
      reviewer_note: payload.reviewer_note || null,
    },
    requireAuth: true,
  });
}

// ---------------------------------------------------------------------------
// Extension-Origin CryptoVault CRUD Helpers (Side Panel Only)
// ---------------------------------------------------------------------------

async function handleVaultSaveRecord(payload) {
  const vault = globalThis.CryptoVault;
  if (!vault || typeof vault.getLocalVault !== 'function') {
    throw new Error('CryptoVault runtime unavailable.');
  }
  const sess = await getSessionValues([SESSION_PASSPHRASE_KEY]);
  const passphrase = sess[SESSION_PASSPHRASE_KEY];
  if (!passphrase) {
    throw new Error('Unlock CryptoVault passphrase before saving verified document fields.');
  }

  const now = new Date().toISOString();
  const record = {
    recordId: payload.recordId || crypto.randomUUID(),
    profileId: payload.profileId || 'self',
    profileDisplayName: payload.profileDisplayName || 'Self',
    docType: payload.docType,
    source: payload.source || 'upload',
    extractedFields: payload.extractedFields || {},
    encryptedBlobKey: payload.encryptedBlobKey || `blob_${crypto.randomUUID()}`,
    mimeType: payload.mimeType || 'application/octet-stream',
    createdAt: payload.createdAt || now,
    updatedAt: now,
  };

  await vault.getLocalVault().saveDocumentRecord(record, payload.rawFileBase64 || '', passphrase);
  return { recordId: record.recordId, docType: record.docType, profileId: record.profileId };
}

async function handleVaultListRecords(profileId) {
  const vault = globalThis.CryptoVault;
  if (!vault || typeof vault.getLocalVault !== 'function') {
    throw new Error('CryptoVault runtime unavailable.');
  }
  return vault.getLocalVault().listRecordsByProfile(profileId || 'self');
}

// ---------------------------------------------------------------------------
// Runtime Message Dispatcher
// ---------------------------------------------------------------------------

function isTrustedExtensionPage(sender) {
  if (!sender || sender.id !== chrome.runtime.id) {
    return false;
  }
  // Content scripts always carry sender.tab; extension pages (sidepanel.html) do not.
  return !sender.tab;
}

chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (!request || typeof request.action !== 'string') {
    return false;
  }

  if (!sender || sender.id !== chrome.runtime.id) {
    sendResponse({ success: false, error: 'Unauthorized message sender.' });
    return false;
  }

  // 1. Content Script Vault Bridge: RESOLVE_VAULT_TOKENS
  if (request.action === 'RESOLVE_VAULT_TOKENS') {
    const senderTabId = sender.tab && typeof sender.tab.id === 'number' ? sender.tab.id : undefined;
    resolveApprovedVaultTokens({
      tokens: request.tokens || [],
      approvalGrantId: request.approvalGrantId,
      senderTabId,
    })
      .then((resolved) => sendResponse({ success: true, resolved }))
      .catch((err) => sendResponse({ success: false, error: err && err.message ? err.message : String(err) }));
    return true;
  }

  // All remaining control/API/vault management actions are restricted to extension pages (Side Panel).
  if (!isTrustedExtensionPage(sender)) {
    sendResponse({ success: false, error: 'Restricted action: extension page context required.' });
    return false;
  }

  (async () => {
    switch (request.action) {
      case 'GET_SESSION_STATE':
        return getSessionState();

      case 'SAVE_SETTINGS':
        return saveSettings(request.settings || {});

      case 'VAULT_UNLOCK': {
        const pass = typeof request.passphrase === 'string' ? request.passphrase : '';
        if (!pass) throw new Error('Passphrase cannot be empty.');
        await setSessionValues({ [SESSION_PASSPHRASE_KEY]: pass });
        return { unlocked: true };
      }

      case 'VAULT_LOCK':
        await removeSessionKeys([SESSION_PASSPHRASE_KEY, SESSION_GRANTS_KEY]);
        return { unlocked: false };

      case 'VAULT_SAVE_RECORD':
        return handleVaultSaveRecord(request.payload || {});

      case 'VAULT_LIST_RECORDS':
        return handleVaultListRecords(request.profileId);

      case 'API_USERS_AUTH':
        return apiUsersAuth(request.payload || {});

      case 'API_VERIFY_PORTAL':
        return apiVerifyPortal(request.url || '');

      case 'API_EXTRACT_AND_VALIDATE':
        return apiExtractAndValidateDocument(request.payload || {});

      case 'API_AGENT_CHAT':
        return apiAgentChat(request.payload || {});

      case 'API_GET_TASK':
        return apiGetTask(request.taskId);

      case 'API_APPROVE_TASK':
        return apiApproveTask(request.taskId, request.payload || {});

      case 'DISPATCH_APPROVED_ACTIONS': {
        const actions = Array.isArray(request.actions) ? request.actions : [];
        const tokens = actions
          .map((a) => a && a.value_or_vault_ref)
          .filter((v) => typeof v === 'string' && v.startsWith('vault:'));
        const grant = await issueApprovalGrant({
          tabId: request.tabId,
          taskId: request.taskId,
          actionHash: request.actionHash || null,
          tokens,
        });
        const tabResponse = await chrome.tabs.sendMessage(request.tabId, {
          action: 'EXECUTE_ACTIONS',
          actions,
          taskId: request.taskId || null,
          approvalGrantId: grant.grantId,
        });
        return { grantId: grant.grantId, tabResponse };
      }

      default:
        throw new Error(`Unknown background action: ${request.action}`);
    }
  })()
    .then((data) => sendResponse({ success: true, data }))
    .catch((err) => sendResponse({ success: false, error: err && err.message ? err.message : String(err) }));

  return true;
});
