/**
 * Action Executor - Content Script for Universal AI Web Assistant
 *
 * Security & Automation Architecture:
 * 1. Zero direct IndexedDB / Local Vault access inside webpage origins.
 *    All `vault:<profileId>.<docType>.<fieldKey>` tokens are batch-resolved
 *    exclusively by `background.js` via `chrome.runtime.sendMessage`
 *    (`RESOLVE_VAULT_TOKENS`), gated by `approvedTaskHashes` & FR-037.
 * 2. Framework-safe filling using native prototype value/checked setters and
 *    bubbling `input`, `change`, and `blur` events for React/Vue/Angular.
 * 3. Post-fill read-back verification (`verifyFilledFields`) comparing actual
 *    DOM `.value` / `.checked` state against intended values.
 * 4. Explicit `CLICK_BUTTON` execution only when included in approved actions.
 */

(function () {
  'use strict';

  function hasExtensionBridge() {
    return (
      typeof chrome !== 'undefined' &&
      chrome.runtime &&
      typeof chrome.runtime.sendMessage === 'function'
    );
  }

  /**
   * Collects unique `vault:` tokens referenced in a list of actions.
   */
  function collectVaultTokens(actions) {
    return Array.from(
      new Set(
        (actions || [])
          .filter(
            (a) =>
              a &&
              a.action_type === 'FILL_FIELD' &&
              typeof a.value_or_vault_ref === 'string' &&
              a.value_or_vault_ref.startsWith('vault:')
          )
          .map((a) => a.value_or_vault_ref)
      )
    );
  }

  /**
   * Requests decrypted vault values from `background.js` via `RESOLVE_VAULT_TOKENS`.
   * Aborts with an error if the task is unapproved or FR-037 verification fails.
   */
  async function requestBackgroundVaultTokens(tokens, options) {
    if (!tokens || tokens.length === 0) {
      return {};
    }
    if (options && options.resolvedTokens) {
      return options.resolvedTokens;
    }
    if (!hasExtensionBridge()) {
      throw new Error('Chrome extension runtime bridge is unavailable for vault token resolution.');
    }

    const opts = options || {};
    const taskId = opts.task_id || opts.taskId || null;
    const actionHash = opts.action_hash || opts.actionHash || null;

    return new Promise((resolve, reject) => {
      const payload = {
        action: 'RESOLVE_VAULT_TOKENS',
        type: 'RESOLVE_VAULT_TOKENS',
        task_id: taskId,
        action_hash: actionHash,
        tokens,
      };
      if (typeof opts.passphrase === 'string' && opts.passphrase.length > 0) {
        payload.passphrase = opts.passphrase;
      }

      chrome.runtime.sendMessage(payload, (response) => {
        if (chrome.runtime.lastError) {
          reject(new Error(chrome.runtime.lastError.message));
          return;
        }
        const ok = Boolean(response && (response.ok === true || response.success === true));
        if (!ok) {
          reject(
            new Error(
              (response && response.error) ||
                'Vault token resolution rejected by background service worker.'
            )
          );
          return;
        }
        resolve(response.resolved || {});
      });
    });
  }

  /**
   * Resolves a literal value or a `vault:` token via the background service worker.
   */
  async function resolveValue(valueOrVaultRef, options) {
    if (valueOrVaultRef === null || valueOrVaultRef === undefined) return '';

    if (typeof valueOrVaultRef === 'string' && valueOrVaultRef.startsWith('vault:')) {
      if (
        options &&
        options.resolvedTokens &&
        Object.prototype.hasOwnProperty.call(options.resolvedTokens, valueOrVaultRef)
      ) {
        return options.resolvedTokens[valueOrVaultRef];
      }

      const resolvedMap = await requestBackgroundVaultTokens([valueOrVaultRef], options);
      const resolved = resolvedMap[valueOrVaultRef];
      if (resolved === null || resolved === undefined) {
        throw new Error(`Vault token '${valueOrVaultRef}' could not be resolved.`);
      }
      return resolved;
    }

    return valueOrVaultRef;
  }

  /**
   * Locates element by CSS selector (prioritizing explicit selector, with element_key fallback).
   */
  function findElement(target) {
    if (!target) return null;
    if (target.selector) {
      try {
        const bySelector = document.querySelector(target.selector);
        if (bySelector) return bySelector;
      } catch (e) {
        console.warn(`[ActionExecutor] Invalid selector: ${target.selector}`, e);
      }
    }
    if (target.element_key) {
      return document.querySelector(`[data-agy-key="${CSS.escape(target.element_key)}"]`);
    }
    return null;
  }

  function normalizeDateValueForHtmlInput(rawVal) {
    const str = String(rawVal ?? '').trim();
    if (/^\d{4}-\d{2}-\d{2}$/.test(str)) {
      return str;
    }
    const ymdSlash = str.match(/^(\d{4})[/\-.](\d{1,2})[/\-.](\d{1,2})$/);
    if (ymdSlash) {
      return `${ymdSlash[1]}-${ymdSlash[2].padStart(2, '0')}-${ymdSlash[3].padStart(2, '0')}`;
    }
    const dmy = str.match(/^(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})$/);
    if (dmy) {
      return `${dmy[3]}-${dmy[2].padStart(2, '0')}-${dmy[1].padStart(2, '0')}`;
    }
    const monMap = {
      jan: '01', feb: '02', mar: '03', apr: '04', may: '05', jun: '06',
      jul: '07', aug: '08', sep: '09', oct: '10', nov: '11', dec: '12',
    };
    const dMonY = str.match(/^(\d{1,2})[\s/\-]+([A-Za-z]{3,9})[\s/\-,]+(\d{4})$/);
    if (dMonY) {
      const m = monMap[dMonY[2].slice(0, 3).toLowerCase()];
      if (m) {
        return `${dMonY[3]}-${m}-${dMonY[1].padStart(2, '0')}`;
      }
    }
    return str;
  }

  /**
   * Dispatches React/Vue/Angular-compatible native value/checked updates and events.
   */
  function setNativeControlValue(element, val) {
    const tag = element.tagName ? element.tagName.toLowerCase() : '';
    const inputType = tag === 'input' ? (element.type || 'text').toLowerCase() : tag;

    if (tag === 'input' && (inputType === 'checkbox' || inputType === 'radio')) {
      const desiredChecked =
        typeof val === 'boolean'
          ? val
          : /^(true|1|yes|on|checked)$/i.test(String(val || '').trim()) ||
            String(val || '') === element.value;
      const descriptor = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'checked');
      if (descriptor && descriptor.set) {
        descriptor.set.call(element, desiredChecked);
      } else {
        element.checked = desiredChecked;
      }
    } else if (tag === 'select') {
      const descriptor = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value');
      let targetValue = String(val ?? '');
      const hasMatchingValue = Array.from(element.options || []).some((o) => o.value === targetValue);
      if (!hasMatchingValue) {
        const byText = Array.from(element.options || []).find(
          (o) => o.text.trim().toLowerCase() === targetValue.trim().toLowerCase()
        );
        if (byText) {
          targetValue = byText.value;
        }
      }
      if (descriptor && descriptor.set) {
        descriptor.set.call(element, targetValue);
      } else {
        element.value = targetValue;
      }
    } else if (tag === 'textarea') {
      const descriptor = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value');
      if (descriptor && descriptor.set) {
        descriptor.set.call(element, String(val ?? ''));
      } else {
        element.value = String(val ?? '');
      }
    } else if (tag === 'input') {
      const descriptor = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value');
      const finalVal = inputType === 'date' ? normalizeDateValueForHtmlInput(val) : String(val ?? '');
      if (descriptor && descriptor.set) {
        descriptor.set.call(element, finalVal);
      } else {
        element.value = finalVal;
      }
    } else {
      element.textContent = String(val ?? '');
    }

    element.dispatchEvent(new Event('input', { bubbles: true, cancelable: true }));
    element.dispatchEvent(new Event('change', { bubbles: true, cancelable: true }));
    element.dispatchEvent(new Event('blur', { bubbles: true, cancelable: true }));
  }

  function readControlState(element) {
    if (!element) {
      return { kind: 'missing', value: null, checked: null };
    }
    const tag = element.tagName ? element.tagName.toLowerCase() : '';
    const inputType = tag === 'input' ? (element.type || 'text').toLowerCase() : tag;

    if (tag === 'input' && (inputType === 'checkbox' || inputType === 'radio')) {
      return {
        kind: 'checked',
        value: element.value,
        checked: Boolean(element.checked),
      };
    }
    if (tag === 'input' || tag === 'select' || tag === 'textarea') {
      return {
        kind: 'value',
        value: typeof element.value === 'string' ? element.value : '',
        checked: null,
      };
    }
    return {
      kind: 'text',
      value: (element.textContent || '').trim(),
      checked: null,
    };
  }

  /**
   * Performs post-fill read-back verification against the live DOM.
   */
  function verifyFilledFields(expectations) {
    const items = [];
    const mismatches = [];
    let verifiedCount = 0;

    for (const exp of expectations || []) {
      const el = exp && exp.element ? exp.element : findElement(exp);
      const isSensitive = Boolean(exp && exp.is_sensitive);
      const expectedRaw =
        exp && Object.prototype.hasOwnProperty.call(exp, 'expected_value')
          ? exp.expected_value
          : exp && exp.expected_value_or_ref;

      if (!el) {
        const mismatch = {
          element_key: (exp && exp.element_key) || null,
          selector: (exp && exp.selector) || null,
          reason: 'element_not_found',
          expected: isSensitive ? '***[LOCAL_MASKED]***' : expectedRaw,
          actual: null,
        };
        mismatches.push(mismatch);
        items.push({ ...mismatch, is_matched: false });
        continue;
      }

      const state = readControlState(el);
      let matched = false;
      let actualForReport = state.value;

      if (state.kind === 'checked') {
        if (typeof expectedRaw === 'boolean') {
          matched = state.checked === expectedRaw;
        } else if (/^(true|false|1|0|yes|no|on|off|checked)$/i.test(String(expectedRaw || '').trim())) {
          const desired = /^(true|1|yes|on|checked)$/i.test(String(expectedRaw).trim());
          matched = state.checked === desired;
        } else {
          matched = state.checked && String(state.value) === String(expectedRaw ?? '');
        }
        actualForReport = state.checked;
      } else {
        const isDateInput =
          el.tagName && el.tagName.toLowerCase() === 'input' && (el.type || '').toLowerCase() === 'date';
        const expectedNormalized = isDateInput
          ? normalizeDateValueForHtmlInput(expectedRaw)
          : String(expectedRaw ?? '');
        matched = String(state.value ?? '') === expectedNormalized;
      }

      if (matched) {
        verifiedCount += 1;
        items.push({
          element_key: (exp && exp.element_key) || null,
          selector: (exp && exp.selector) || null,
          is_matched: true,
          actual: isSensitive ? '***[LOCAL_MASKED]***' : actualForReport,
        });
      } else {
        const mismatch = {
          element_key: (exp && exp.element_key) || null,
          selector: (exp && exp.selector) || null,
          reason: 'value_mismatch',
          expected: isSensitive ? '***[LOCAL_MASKED]***' : expectedRaw,
          actual: isSensitive ? '***[LOCAL_MASKED]***' : actualForReport,
        };
        mismatches.push(mismatch);
        items.push({ ...mismatch, is_matched: false });
      }
    }

    return {
      ok: mismatches.length === 0,
      verified_count: verifiedCount,
      mismatches,
      items,
    };
  }

  /**
   * Executes a single approved ClientAction.
   */
  async function executeSingleAction(action, options) {
    const el = findElement(action);
    if (!el && action.action_type !== 'NAVIGATE') {
      return {
        action,
        success: false,
        error: `Element not found for selector: ${action.selector} / key: ${action.element_key}`,
      };
    }

    try {
      switch (action.action_type) {
        case 'FILL_FIELD': {
          const resolvedValue = await resolveValue(action.value_or_vault_ref, options);

          if (typeof el.scrollIntoView === 'function') {
            el.scrollIntoView({ behavior: 'smooth', block: 'center' });
          }
          if (typeof el.focus === 'function') {
            el.focus();
          }

          setNativeControlValue(el, resolvedValue);

          const verification = verifyFilledFields([
            {
              element: el,
              element_key: action.element_key || null,
              selector: action.selector || null,
              expected_value: resolvedValue,
              is_sensitive: Boolean(action.is_sensitive),
            },
          ]);
          const isVerified = verification.verified_count === 1;
          const readback = readControlState(el);
          const actualVal = readback.kind === 'checked' ? String(readback.checked) : String(readback.value ?? '');

          if (el.style) {
            el.style.transition = 'outline 0.3s';
            el.style.outline = isVerified ? '2px solid #059669' : '2px solid #dc2626';
            setTimeout(() => {
              el.style.outline = '';
            }, 1200);
          }

          return {
            action,
            success: isVerified,
            verified: isVerified,
            expected_value: resolvedValue,
            readback_value: action.is_sensitive ? '***[LOCAL_MASKED]***' : actualVal,
            error: isVerified ? undefined : 'Post-fill read-back verification failed.',
          };
        }

        case 'FOCUS_INPUT': {
          if (typeof el.scrollIntoView === 'function') {
            el.scrollIntoView({ behavior: 'smooth', block: 'center' });
          }
          if (typeof el.focus === 'function') {
            el.focus();
          }
          if (el.style) {
            el.style.outline = '2px solid #1a56db';
            setTimeout(() => {
              el.style.outline = '';
            }, 1500);
          }
          return { action, success: true };
        }

        case 'TRIGGER_FILE_PICKER': {
          if (typeof el.scrollIntoView === 'function') {
            el.scrollIntoView({ behavior: 'smooth', block: 'center' });
          }
          if (typeof el.focus === 'function') {
            el.focus();
          }
          el.click();
          return { action, success: true };
        }

        case 'CLICK_BUTTON': {
          if (typeof el.scrollIntoView === 'function') {
            el.scrollIntoView({ behavior: 'smooth', block: 'center' });
          }
          if (typeof el.focus === 'function') {
            el.focus();
          }
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
      return {
        action,
        success: false,
        error: err && err.message ? err.message : String(err),
      };
    }
  }

  /**
   * Batch-resolves any `vault:` tokens via `background.js`, executes approved actions,
   * and performs post-fill read-back verification (`verifyFilledFields`).
   */
  async function executeActions(actions, options) {
    let scopedOptions = options ? { ...options } : {};
    const vaultTokens = collectVaultTokens(actions);

    if (vaultTokens.length > 0 && !scopedOptions.resolvedTokens) {
      // Abort immediately if background.js rejects (unapproved task or FR-037 violation).
      const resolvedTokens = await requestBackgroundVaultTokens(vaultTokens, scopedOptions);
      scopedOptions = { ...scopedOptions, resolvedTokens };
    }

    const results = [];
    const fillExpectations = [];

    for (const act of actions || []) {
      const res = await executeSingleAction(act, scopedOptions);
      if (act && act.action_type === 'FILL_FIELD') {
        fillExpectations.push({
          element_key: act.element_key || null,
          selector: act.selector || null,
          expected_value: res.expected_value,
          is_sensitive: Boolean(act.is_sensitive),
        });
      }
      const { expected_value: _omitExpected, ...publicResult } = res;
      results.push(publicResult);
      await new Promise((r) => setTimeout(r, 80));
    }

    const verification = verifyFilledFields(fillExpectations);
    return {
      ok: true,
      success: true,
      results,
      verified_count: verification.verified_count,
      mismatches: verification.mismatches,
    };
  }

  /**
   * Scans DOM for post-submit acknowledgement receipt number.
   */
  function captureReceipt() {
    const text = document.body ? document.body.innerText || document.body.textContent || '' : '';
    const match = text.match(/\b(APP-\d{4}-\d{4,6}|ACK-\d{6,10}|ECI-\d{4}-\d{4,6})\b/i);
    const receiptRef = match ? match[1] : null;
    const receiptBox =
      document.getElementById('acknowledgement-receipt') || document.querySelector('.receipt-box');

    return {
      receipt_number: receiptRef,
      has_receipt_card: !!receiptBox,
      timestamp: new Date().toISOString(),
    };
  }

  const api = {
    executeActions,
    executeSingleAction,
    resolveValue,
    verifyFilledFields,
    captureReceipt,
  };

  if (typeof window !== 'undefined') {
    window.__UA_ACTION_EXECUTOR__ = api;
    window.ActionExecutor = api;
  }
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }

  if (typeof chrome !== 'undefined' && chrome.runtime && chrome.runtime.onMessage) {
    chrome.runtime.onMessage.addListener((request, _sender, sendResponse) => {
      if (!request) return false;

      const msgType = request.type || request.action;
      if (msgType === 'EXECUTE_APPROVED_ACTIONS' || msgType === 'EXECUTE_ACTIONS') {
        const execOptions = {
          task_id: request.task_id || request.taskId || null,
          action_hash: request.action_hash || request.actionHash || null,
          passphrase: request.passphrase,
        };
        executeActions(request.actions || [], execOptions)
          .then((outcome) => {
            sendResponse(outcome);
          })
          .catch((err) => {
            sendResponse({
              ok: false,
              success: false,
              error: err && err.message ? err.message : String(err),
              results: [],
              verified_count: 0,
              mismatches: [],
            });
          });
        return true;
      }

      if (msgType === 'VERIFY_FILLED_FIELDS') {
        const verification = verifyFilledFields(request.expectations || request.items || []);
        sendResponse({
          ok: verification.ok,
          success: verification.ok,
          verified_count: verification.verified_count,
          mismatches: verification.mismatches,
          items: verification.items,
        });
        return true;
      }

      if (msgType === 'CAPTURE_RECEIPT') {
        const receipt = captureReceipt();
        sendResponse({ ok: true, success: true, receipt });
        return true;
      }

      return false;
    });
  }

  console.log('[Universal AI Assistant] Action Executor Content Script Loaded (Background Vault Bridge).');
})();
