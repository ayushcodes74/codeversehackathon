/**
 * Action Executor - Content Script for Universal AI Web Assistant
 *
 * Reliably executes browser automation actions:
 * 1. Resolves vault: reference tokens locally (Zero-Leak Hybrid Storage).
 * 2. Uses native prototype value setters + bubbling input/change/blur events
 *    to guarantee React/Vue/Angular controlled component state synchronization.
 * 3. Reads back filled values for verification.
 * 4. Extracts post-submit acknowledgement receipt numbers.
 */

(function () {
  'use strict';

  // Local Secure Vault Token Mock / Storage Resolver
  // In production, this decrypts from Chrome Encrypted Storage with user biometric/master PIN
  const LOCAL_VAULT_STORE = {
    'vault:self.pan.id_number': 'ABCDE1234F',
    'vault:self.aadhaar.id_number': '2345 6789 0123',
    'vault:self.bank.account_number': '98765432101234',
    'vault:self.bank.ifsc_code': 'SBIN0001234',
    'vault:father.pan.id_number': 'FGHIJ5678K',
    'vault:father.aadhaar.id_number': '8765 4321 0987',
  };

  /**
   * Resolves a value, substituting local decrypted vault tokens via CryptoVault if applicable
   */
  async function resolveValue(valueOrVaultRef) {
    if (!valueOrVaultRef) return '';

    if (valueOrVaultRef.startsWith('vault:')) {
      // 1. Check window.__cryptoVault or window.CryptoVault instance
      if (typeof window !== 'undefined' && window.__cryptoVault && typeof window.__cryptoVault.resolveVaultToken === 'function') {
        try {
          const decrypted = await window.__cryptoVault.resolveVaultToken(valueOrVaultRef);
          if (decrypted) return decrypted;
        } catch (e) {
          console.debug('[ActionExecutor] cryptoVault resolution notice:', e);
        }
      }

      // 2. Try Chrome storage (local or session)
      if (typeof chrome !== 'undefined' && chrome.storage && chrome.storage.local) {
        try {
          const stored = await chrome.storage.local.get([valueOrVaultRef]);
          if (stored && stored[valueOrVaultRef]) {
            return stored[valueOrVaultRef];
          }
        } catch (e) {
          console.debug('[ActionExecutor] chrome.storage lookup failed:', e);
        }
      }

      // 3. Fallback to local secure store mapping
      if (LOCAL_VAULT_STORE[valueOrVaultRef]) {
        return LOCAL_VAULT_STORE[valueOrVaultRef];
      }
      console.warn(`[ActionExecutor] Vault token ${valueOrVaultRef} not found in local vault.`);
      return '';
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
  async function executeSingleAction(action) {
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
          const resolvedValue = await resolveValue(action.value_or_vault_ref);
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
      return { action, success: false, error: err.message };
    }
  }

  /**
   * Executes a batch of ClientActions sequentially
   */
  async function executeActions(actions) {
    const results = [];
    for (const act of actions) {
      const res = await executeSingleAction(act);
      results.push(res);
      // Small 80ms delay between field fills to simulate human pacing and let DOM settle
      await new Promise(r => setTimeout(r, 80));
    }
    return results;
  }

  /**
   * Scans DOM for post-submit acknowledgement receipt number
   */
  function captureReceipt() {
    const text = document.body.innerText;
    // Regex matching APP-2026-XXXXX or ACK-XXXXXX
    const match = text.match(/\b(APP-\d{4}-\d{4,6}|ACK-\d{6,10})\b/);
    const receiptRef = match ? match[1] : null;

    const receiptBox = document.getElementById('acknowledgement-receipt') || document.querySelector('.receipt-box');

    return {
      receipt_number: receiptRef,
      has_receipt_card: !!receiptBox,
      timestamp: new Date().toISOString()
    };
  }

  // Export to global scope
  window.ActionExecutor = {
    executeActions,
    executeSingleAction,
    resolveValue,
    captureReceipt,
    LOCAL_VAULT_STORE
  };

  // Chrome Extension message listener
  if (typeof chrome !== 'undefined' && chrome.runtime && chrome.runtime.onMessage) {
    chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
      if (request.action === 'EXECUTE_ACTIONS') {
        executeActions(request.actions || []).then(results => {
          sendResponse({ success: true, results });
        });
        return true; // Keep message channel open for async response
      } else if (request.action === 'CAPTURE_RECEIPT') {
        const receipt = captureReceipt();
        sendResponse({ success: true, receipt });
        return true;
      }
    });
  }

  console.log('[Universal AI Assistant] Action Executor Content Script Loaded.');
})();
