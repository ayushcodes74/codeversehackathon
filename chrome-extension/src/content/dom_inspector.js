/**
 * DOM Inspector - Content Script for Universal AI Web Assistant
 *
 * Scans visible interactive form controls (standard HTML + ARIA-roled widgets)
 * and actionable buttons/links, extracting Accessibility-Tree metadata alongside
 * deterministic CSS selectors into a DOMSnapshot schema matching
 * backend/app/schemas/agent_contracts.py and backend/app/agent/orchestrator.py.
 */

(function () {
  'use strict';

  const MAX_SNIPPET_CHARS = 1500;
  const FORM_CONTROL_SELECTOR = [
    'input',
    'select',
    'textarea',
    '[role="textbox"]',
    '[role="combobox"]',
    '[role="checkbox"]',
    '[role="radio"]'
  ].join(', ');

  const ACTION_CONTROL_SELECTOR = [
    'button',
    'input[type="button"]',
    'input[type="submit"]',
    'input[type="reset"]',
    'a[href]',
    '[role="button"]'
  ].join(', ');

  const NON_FIELD_INPUT_TYPES = new Set(['hidden', 'submit', 'button', 'reset', 'image']);

  /**
   * Generates a stable unique CSS selector for an element, prioritizing #id,
   * unique [name="..."], and deterministic DOM path traversal fallback.
   */
  function getUniqueSelector(el) {
    if (!el || el.nodeType !== Node.ELEMENT_NODE) return '';

    if (el.id) {
      const idSel = `#${CSS.escape(el.id)}`;
      try {
        if (document.querySelectorAll(idSel).length === 1) {
          return idSel;
        }
      } catch (_err) {
        return idSel;
      }
    }

    const nameAttr = el.getAttribute('name');
    if (nameAttr) {
      const nameSel = `${el.tagName.toLowerCase()}[name="${CSS.escape(nameAttr)}"]`;
      try {
        if (document.querySelectorAll(nameSel).length === 1) {
          return nameSel;
        }
      } catch (_err) {
        return nameSel;
      }
    }

    const path = [];
    let current = el;
    while (current && current.nodeType === Node.ELEMENT_NODE && current !== document.body && current !== document.documentElement) {
      let segment = current.tagName.toLowerCase();
      if (current.id) {
        segment = `#${CSS.escape(current.id)}`;
        path.unshift(segment);
        break;
      }
      if (current.className && typeof current.className === 'string') {
        const firstClass = current.className.trim().split(/\s+/)[0];
        if (firstClass) {
          segment += `.${CSS.escape(firstClass)}`;
        }
      }
      let sibling = current;
      let nth = 1;
      while ((sibling = sibling.previousElementSibling)) {
        if (sibling.tagName === current.tagName) {
          nth += 1;
        }
      }
      if (nth > 1) {
        segment += `:nth-of-type(${nth})`;
      }
      path.unshift(segment);
      current = current.parentElement;
    }
    return path.join(' > ');
  }

  /**
   * Checks whether an element is visible and interactive to the user.
   */
  function isVisible(el) {
    if (!el || el.nodeType !== Node.ELEMENT_NODE) return false;
    if (el.hidden || el.getAttribute('aria-hidden') === 'true') return false;
    if (el.tagName && el.tagName.toLowerCase() === 'input' && el.type === 'hidden') return false;

    const style = window.getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') {
      return false;
    }
    const rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  }

  function cleanText(raw) {
    return String(raw || '').replace(/\s+/g, ' ').trim();
  }

  /**
   * Resolves text referenced by space-separated IDs in aria-labelledby.
   */
  function getAriaLabelledByText(el) {
    const labelledby = el.getAttribute('aria-labelledby');
    if (!labelledby) return '';
    const parts = labelledby
      .trim()
      .split(/\s+/)
      .map((id) => {
        const refEl = document.getElementById(id);
        return refEl ? cleanText(refEl.innerText || refEl.textContent) : '';
      })
      .filter(Boolean);
    return parts.join(' ').trim();
  }

  /**
   * Extracts text from a wrapping <label> excluding embedded input/select/textarea option text.
   */
  function getWrappingLabelText(el) {
    const parentLabel = el.closest('label');
    if (!parentLabel) return '';
    const clone = parentLabel.cloneNode(true);
    if (clone && typeof clone.querySelectorAll === 'function') {
      clone.querySelectorAll('input, select, textarea').forEach((child) => child.remove());
    }
    const extracted = cleanText(clone.textContent || parentLabel.innerText);
    return extracted || cleanText(parentLabel.innerText || parentLabel.textContent);
  }

  /**
   * Extracts the best associated human-readable label for a form control.
   */
  function getElementLabel(el) {
    // 1. Explicit <label for="id">
    if (el.id) {
      const labelEl = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (labelEl) {
        const text = cleanText(labelEl.innerText || labelEl.textContent);
        if (text) return text;
      }
    }

    // 2. Wrapping <label>
    const wrappingText = getWrappingLabelText(el);
    if (wrappingText) return wrappingText;

    // 3. aria-labelledby referenced element text
    const labelledByText = getAriaLabelledByText(el);
    if (labelledByText) return labelledByText;

    // 4. aria-label
    const ariaLabel = cleanText(el.getAttribute('aria-label'));
    if (ariaLabel) return ariaLabel;

    // 5. placeholder
    const placeholder = cleanText(el.getAttribute('placeholder') || el.placeholder);
    if (placeholder) return placeholder;

    // 6. Preceding sibling label/span text
    const prev = el.previousElementSibling;
    if (prev && (prev.tagName === 'LABEL' || prev.tagName === 'SPAN')) {
      const prevText = cleanText(prev.innerText || prev.textContent);
      if (prevText) return prevText;
    }

    return cleanText(el.getAttribute('name') || el.id || '');
  }

  /**
   * Resolves explicit role or WAI-ARIA implicit role for an HTML control.
   */
  function resolveAriaRole(el) {
    const explicit = cleanText(el.getAttribute('role'));
    if (explicit) return explicit;

    const tag = el.tagName.toLowerCase();
    if (tag === 'button') return 'button';
    if (tag === 'a' && el.hasAttribute('href')) return 'link';
    if (tag === 'select') return el.multiple ? 'listbox' : 'combobox';
    if (tag === 'textarea') return 'textbox';
    if (tag === 'input') {
      const t = (el.type || 'text').toLowerCase();
      if (t === 'checkbox') return 'checkbox';
      if (t === 'radio') return 'radio';
      if (t === 'button' || t === 'submit' || t === 'reset') return 'button';
      if (t === 'number' || t === 'range') return 'spinbutton';
      if (t === 'search') return 'searchbox';
      return 'textbox';
    }
    return null;
  }

  function resolveInputType(el) {
    const tag = el.tagName.toLowerCase();
    if (tag === 'input') return (el.type || 'text').toLowerCase();
    if (tag === 'select') return 'select';
    if (tag === 'textarea') return 'textarea';
    return cleanText(el.getAttribute('role')) || tag;
  }

  function resolveFieldValue(el, inputType) {
    if (inputType === 'password') {
      const raw = typeof el.value === 'string' ? el.value : '';
      return raw.length > 0 ? '***[MASKED]***' : null;
    }
    if (inputType === 'checkbox' || inputType === 'radio') {
      const isChecked =
        typeof el.checked === 'boolean'
          ? el.checked
          : el.getAttribute('aria-checked') === 'true';
      return isChecked ? (el.value || 'true') : null;
    }
    if (typeof el.value === 'string') {
      return el.value || null;
    }
    if (el.isContentEditable || el.getAttribute('role') === 'textbox') {
      const txt = cleanText(el.textContent);
      return txt || null;
    }
    return null;
  }

  function parseAriaInvalid(el) {
    const raw = cleanText(el.getAttribute('aria-invalid')).toLowerCase();
    if (!raw || raw === 'false') return false;
    if (raw === 'true') return true;
    return raw;
  }

  function detectStepMetadata() {
    let stepIndicator = null;
    let stepNum = 1;
    let totalSteps = 1;

    const stepHeading = document.querySelector('h1, h2, .step-title, [id*="step-heading"]');
    if (stepHeading && /step/i.test(stepHeading.innerText || '')) {
      stepIndicator = cleanText(stepHeading.innerText);
      const match = stepIndicator.match(/Step\s*(\d+)\s*(?:of|\/)\s*(\d+)/i);
      if (match) {
        stepNum = parseInt(match[1], 10);
        totalSteps = parseInt(match[2], 10);
      }
    } else {
      const activeNav = document.querySelector('.step-item.active');
      if (activeNav) {
        stepIndicator = cleanText(activeNav.innerText);
      }
      const allSteps = document.querySelectorAll('.step-item');
      if (allSteps.length > 0) {
        totalSteps = allSteps.length;
      }
    }

    return { stepIndicator, stepNum, totalSteps };
  }

  /**
   * Captures a structured DOMSnapshot with Accessibility-Tree metadata
   * compatible with backend/app/agent/orchestrator.py.
   */
  function captureDOMSnapshot() {
    const rawControls = Array.from(document.querySelectorAll(FORM_CONTROL_SELECTOR));
    const seenFields = new Set();
    const fields = [];
    let fieldIndex = 1;

    for (const el of rawControls) {
      if (seenFields.has(el)) continue;
      seenFields.add(el);

      const tag = el.tagName.toLowerCase();
      if (tag === 'input' && NON_FIELD_INPUT_TYPES.has((el.type || '').toLowerCase())) {
        continue;
      }
      if (!isVisible(el)) continue;

      let key = el.dataset && el.dataset.agyKey;
      if (!key) {
        key = `field_${String(fieldIndex).padStart(2, '0')}`;
        if (el.dataset) {
          el.dataset.agyKey = key;
        }
        fieldIndex += 1;
      }

      const inputType = resolveInputType(el);
      const rect = el.getBoundingClientRect();
      const options =
        tag === 'select'
          ? Array.from(el.options).map((o) => cleanText(o.text))
          : [];

      const ariaLabel = cleanText(el.getAttribute('aria-label')) || null;
      const ariaLabelledBy = cleanText(el.getAttribute('aria-labelledby')) || null;
      const ariaRequired = Boolean(el.required || el.getAttribute('aria-required') === 'true');
      const ariaInvalid = parseAriaInvalid(el);
      const isDisabled = Boolean(el.disabled || el.getAttribute('aria-disabled') === 'true');
      const isReadOnly = Boolean(el.readOnly || el.getAttribute('aria-readonly') === 'true');
      const currentValue = resolveFieldValue(el, inputType);

      fields.push({
        element_key: key,
        selector: getUniqueSelector(el),
        tag,
        tag_name: tag,
        input_type: inputType,
        label: getElementLabel(el),
        name: el.getAttribute('name') || null,
        id: el.id || null,
        placeholder: el.getAttribute('placeholder') || el.placeholder || null,
        aria_role: resolveAriaRole(el),
        aria_label: ariaLabel,
        aria_labelledby: ariaLabelledBy,
        aria_required: ariaRequired,
        aria_invalid: ariaInvalid,
        is_required: ariaRequired,
        disabled: isDisabled,
        is_disabled: isDisabled,
        readonly: isReadOnly,
        hidden: false,
        value: currentValue,
        current_value: currentValue,
        checked:
          inputType === 'checkbox' || inputType === 'radio'
            ? Boolean(el.checked || el.getAttribute('aria-checked') === 'true')
            : undefined,
        options,
        bounding_box: {
          x: Math.round(rect.x + window.scrollX),
          y: Math.round(rect.y + window.scrollY),
          width: Math.round(rect.width),
          height: Math.round(rect.height)
        }
      });
    }

    const rawActionables = Array.from(document.querySelectorAll(ACTION_CONTROL_SELECTOR));
    const seenButtons = new Set();
    const buttons = [];
    let btnIndex = 1;

    for (const btn of rawActionables) {
      if (seenButtons.has(btn)) continue;
      seenButtons.add(btn);
      if (!isVisible(btn)) continue;

      const text = cleanText(
        btn.innerText ||
          btn.textContent ||
          btn.getAttribute('aria-label') ||
          btn.value ||
          ''
      );
      const tag = btn.tagName.toLowerCase();
      const btnType = tag === 'a' ? 'link' : (btn.type || 'button').toLowerCase();
      const isSubmit =
        btnType === 'submit' ||
        /\b(submit|confirm\s+and\s+submit|final\s+submit)\b/i.test(text);
      const isDisabled = Boolean(btn.disabled || btn.getAttribute('aria-disabled') === 'true');
      const classStr = typeof btn.className === 'string' ? btn.className : '';

      buttons.push({
        element_key: `btn_${String(btnIndex).padStart(2, '0')}`,
        selector: getUniqueSelector(btn),
        text,
        button_type: btnType,
        aria_role: resolveAriaRole(btn),
        disabled: isDisabled,
        is_disabled: isDisabled,
        is_submit: isSubmit,
        is_primary: classStr.includes('primary') || classStr.includes('success') || isSubmit
      });
      btnIndex += 1;
    }

    const { stepIndicator, stepNum, totalSteps } = detectStepMetadata();
    const rawBodyText = document.body ? (document.body.innerText || document.body.textContent || '') : '';
    const snippet = rawBodyText.trim().slice(0, MAX_SNIPPET_CHARS);

    return {
      url: window.location.href,
      title: document.title || '',
      step_indicator: stepIndicator,
      current_step_number: stepNum,
      total_steps: totalSteps,
      fields,
      buttons,
      text_content_snippet: snippet,
      timestamp: new Date().toISOString()
    };
  }

  const api = {
    captureDOMSnapshot,
    captureSnapshot: captureDOMSnapshot
  };

  if (typeof window !== 'undefined') {
    window.__UA_DOM_INSPECTOR__ = api;
    window.DOMInspector = api;
  }
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }

  if (typeof chrome !== 'undefined' && chrome.runtime && chrome.runtime.onMessage) {
    chrome.runtime.onMessage.addListener((request, _sender, sendResponse) => {
      if (
        request &&
        (request.type === 'CAPTURE_DOM_SNAPSHOT' ||
          request.action === 'CAPTURE_DOM_SNAPSHOT' ||
          request.action === 'INSPECT_DOM')
      ) {
        const snapshot = captureDOMSnapshot();
        sendResponse({ ok: true, success: true, snapshot });
        return true;
      }
      return false;
    });
  }

  console.log('[Universal AI Assistant] Accessibility-Tree DOM Inspector Loaded.');
})();
