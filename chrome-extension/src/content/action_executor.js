/**
 * Action Executor - Content Script for Universal AI Web Assistant
 *
 * Reliably executes browser automation actions:
 * 1. Resolves canonical `vault:<profileId>.<docType>.<fieldKey>` tokens strictly via
 *    the extension-origin Background Service Worker (`RESOLVE_VAULT_TOKENS` over
 *    `chrome.runtime.sendMessage`), keeping `ua_local_vault_db` and `chrome.storage.session`
 *    isolated inside `chrome-extension://<id>` rather than the portal page origin.
 * 2. Uses native prototype value setters + bubbling input/change/blur events
 *    to guarantee React/Vue/Angular controlled component state synchronization.
 * 3. Reads back filled values for verification.
 * 4. Extracts post-submit acknowledgement receipt numbers.
 */

(function () {
  'use strict';

  function getFallbackVaultModule() {
    // Only used in non-extension Node/unit-test environments where chrome.runtime.sendMessage is absent.
    if (typeof chrome !== 'undefined' && chrome.runtime && typeof chrome.runtime.sendMessage === 'function') {
      return null;
    }
    if (typeof globalThis !== 'undefined' && globalThis.CryptoVault) {
      return globalThis.CryptoVault;
    }
    if (typeof require === 'function') {
      try {
        return require('../../dist/vault/cryptoVault.js');
      } catch (_err) {
        return null;
      }
    }
    return null;
  }

  function hasExtensionBridge() {
    return typeof chrome !== 'undefined' && chrome.runtime && typeof chrome.runtime.sendMessage === 'function';
  }

  /**
   * Requests extension-origin vault resolution from background.js via RESOLVE_VAULT_TOKENS.
   */
  async function requestBackgroundVaultTokens(tokens, options) {
    if (!tokens || tokens.length === 0) {
      return {};
    }
    if (options && options.resolvedTokens) {
      return options.resolvedTokens;
    }

    return new Promise((resolve, reject) => {
      chrome.runtime.sendMessage(
        {
          action: 'RESOLVE_VAULT_TOKENS',
          tokens,
          taskId: options && options.taskId ? options.taskId : null,
          approvalGrantId: options && options.approvalGrantId ? options.approvalGrantId : null,
        },
        (response) => {
          if (chrome.runtime.lastError) {
            reject(new Error(chrome.runtime.lastError.message));
            return;
          }
          if (!response || !response.success) {
            reject(new Error((response && response.error) || 'Failed to resolve vault tokens in background service worker.'));
            return;
          }
          resolve(response.resolved || {});
        }
      );
    });
  }

  /**
   * Resolves a literal value or a `vault:<profileId>.<docType>.<fieldKey>` token.
   */
  async function resolveValue(valueOrVaultRef, options) {
    if (!valueOrVaultRef) return '';

    if (typeof valueOrVaultRef === 'string' && valueOrVaultRef.startsWith('vault:')) {
      if (options && options.resolvedTokens && Object.prototype.hasOwnProperty.call(options.resolvedTokens, valueOrVaultRef)) {
        return options.resolvedTokens[valueOrVaultRef];
      }

      if (options && options.vault && typeof options.vault.resolveVaultToken === 'function') {
        const resolved = await options.vault.resolveVaultToken(valueOrVaultRef, {
          passphrase: options.passphrase,
          allowUnverified: Boolean(options.allowUnverified),
        });
        return resolved == null ? '' : resolved;
      }

      if (hasExtensionBridge()) {
        const resolvedMap = await requestBackgroundVaultTokens([valueOrVaultRef], options);
        const resolved = resolvedMap[valueOrVaultRef];
        if (resolved === null || resolved === undefined) {
          console.warn(`[ActionExecutor] Vault token ${valueOrVaultRef} missing in background response.`);
          return '';
        }
        return resolved;
      }

      const vaultMod = getFallbackVaultModule();
      if (!vaultMod || typeof vaultMod.resolveVaultToken !== 'function') {
        throw new Error('Encrypted CryptoVault bridge is not available.');
      }
      const resolved = await vaultMod.resolveVaultToken(valueOrVaultRef, {
        passphrase: options && options.passphrase,
        allowUnverified: Boolean(options && options.allowUnverified),
      });
      return resolved == null ? '' : resolved;
    }

    return valueOrVaultRef;
  }

  /**
   * Dispatches React-compatible native input value change
   */
  function setNativeInputValue(element, val) {
    let prototype;
    if (element instanceof HTMLSelectElement) {
      prototype = HTMLSelectElement.prototype;
    } else if (element instanceof HTMLTextAreaElement) {
      prototype = HTMLTextAreaElement.prototype;
    } else {
      prototype = HTMLInputElement.prototype;
    }

    const descriptor = Object.getOwnPropertyDescriptor(prototype, 'value');
    if (descriptor && descriptor.set) {
      descriptor.set.call(element, val);
    } else {
      element.value = val;
    }

    // Dispatch full cascade of events for React, Angular, Vue, and vanilla listeners
    element.dispatchEvent(new Event('input', { bubbles: true, cancelable: true }));
    element.dispatchEvent(new Event('change', { bubbles: true, cancelable: true }));
    element.dispatchEvent(new Event('blur', { bubbles: true, cancelable: true }));
  }

  /**
   * Locates element by element_key data attribute or CSS selector
   */
  function findElement(action) {
    if (action.element_key) {
      const byKey = document.querySelector(`[data-agy-key="${CSS.escape(action.element_key)}"]`);
      if (byKey) return byKey;
    }
    if (action.selector) {
      try {
        return document.querySelector(action.selector);
      } catch (e) {
        console.warn(`[ActionExecutor] Invalid selector: ${action.selector}`, e);
      }
    }
    return null;
  }

  /**
   * Executes a single ClientAction
   */
  async function executeSingleAction(action, options) {
    const el = findElement(action);
    if (!el && action.action_type !== 'NAVIGATE') {
      return {
        action,
        success: false,
        error: `Element not found for key: ${action.element_key} / selector: ${action.selector}`
      };
    }

    try {
      switch (action.action_type) {
        case 'FILL_FIELD': {
          const isVaultRef =
            typeof action.value_or_vault_ref === 'string' &&
            action.value_or_vault_ref.startsWith('vault:');
          const resolvedValue = await resolveValue(action.value_or_vault_ref, options);

          if (isVaultRef && !resolvedValue) {
            return {
              action,
              success: false,
              error: `Vault token ${action.value_or_vault_ref} not found in encrypted local vault.`
            };
          }

          el.scrollIntoView({ behavior: 'smooth', block: 'center' });
          el.focus();

          setNativeInputValue(el, resolvedValue);

          // Verification Readback
          const actualVal = el.value;
          const isVerified = actualVal.length > 0;

          // Highlight filled element briefly with subtle outline
          el.style.transition = 'outline 0.3s';
          el.style.outline = '2px solid #059669';
          setTimeout(() => {
            el.style.outline = '';
          }, 1200);

          return {
            action,
            success: isVerified,
            readback_value: action.is_sensitive ? '***[LOCAL_MASKED]***' : actualVal
          };
        }

        case 'FOCUS_INPUT': {
          el.scrollIntoView({ behavior: 'smooth', block: 'center' });
          el.focus();
          el.style.outline = '2px solid #1a56db';
          setTimeout(() => {
            el.style.outline = '';
          }, 1500);
          return { action, success: true };
        }

        case 'TRIGGER_FILE_PICKER': {
          el.scrollIntoView({ behavior: 'smooth', block: 'center' });
          el.click();
          return { action, success: true };
        }

        case 'CLICK_BUTTON': {
          el.scrollIntoView({ behavior: 'smooth', block: 'center' });
          el.click();
          return { action, success: true };
        }

        case 'NAVIGATE': {
          if (action.value_or_vault_ref) {
            window.location.href = action.value_or_vault_ref;
            return { action, success: true };
          }
          return { action, success: false, error: 'No URL specified for navigation' };
        }

        default:
          return { action, success: false, error: `Unsupported action type: ${action.action_type}` };
      }
    } catch (err) {
      return { action, success: false, error: err && err.message ? err.message : String(err) };
    }
  }

  /**
   * Executes a batch of ClientActions sequentially after batch-resolving approved vault tokens
   * from the extension-origin Background Service Worker.
   */
  async function executeActions(actions, options) {
    let scopedOptions = options || {};
    const vaultTokens = Array.from(
      new Set(
        (actions || [])
          .filter((a) => a && a.action_type === 'FILL_FIELD' && typeof a.value_or_vault_ref === 'string' && a.value_or_vault_ref.startsWith('vault:'))
          .map((a) => a.value_or_vault_ref)
      )
    );

    if (vaultTokens.length > 0 && hasExtensionBridge() && !(options && options.vault) && !(options && options.resolvedTokens)) {
      const resolvedTokens = await requestBackgroundVaultTokens(vaultTokens, scopedOptions);
      scopedOptions = { ...scopedOptions, resolvedTokens };
    }

    const results = [];
    for (const act of actions || []) {
      const res = await executeSingleAction(act, scopedOptions);
      results.push(res);
      await new Promise((r) => setTimeout(r, 80));
    }
    return results;
  }

  /**
   * Scans DOM for post-submit acknowledgement receipt number
   */
  function captureReceipt() {
    const text = document.body.innerText || '';
    const match = text.match(/\b(APP-\d{4}-\d{4,6}|ACK-\d{6,10}|ECI-\d{4}-\d{4,6})\b/i);
    const receiptRef = match ? match[1] : null;
    const receiptBox = document.getElementById('acknowledgement-receipt') || document.querySelector('.receipt-box');

    return {
      receipt_number: receiptRef,
      has_receipt_card: !!receiptBox,
      timestamp: new Date().toISOString()
    };
  }

  const fallbackVault = getFallbackVaultModule();
  const api = {
    executeActions,
    executeSingleAction,
    resolveValue,
    captureReceipt,
    CANONICAL_VAULT_KEYS: fallbackVault ? fallbackVault.CANONICAL_VAULT_KEYS : null,
  };

  if (typeof window !== 'undefined') {
    window.ActionExecutor = api;
  }
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }

  if (typeof chrome !== 'undefined' && chrome.runtime && chrome.runtime.onMessage) {
    chrome.runtime.onMessage.addListener((request, _sender, sendResponse) => {
      if (request && request.action === 'EXECUTE_ACTIONS') {
        const execOptions = {
          taskId: request.taskId || null,
          approvalGrantId: request.approvalGrantId || null,
          allowUnverified: Boolean(request.allowUnverified),
        };
        executeActions(request.actions || [], execOptions)
          .then((results) => {
            sendResponse({ success: true, results });
          })
          .catch((err) => {
            sendResponse({ success: false, error: err && err.message ? err.message : String(err) });
          });
        return true;
      } else if (request && request.action === 'CAPTURE_RECEIPT') {
        const receipt = captureReceipt();
        sendResponse({ success: true, receipt });
        return true;
      }
      return false;
    });
  }

  console.log('[Universal AI Assistant] Action Executor Content Script Loaded (SW Vault Bridge).');
})();
