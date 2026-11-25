/**
 * DOM Inspector - Content Script for Universal AI Web Assistant
 *
 * Scans visible interactive inputs, selects, textareas, and buttons.
 * Assigns ephemeral element_key attributes (field_01, field_02)
 * and generates a clean DOMSnapshot schema matching agent_contracts.py.
 */

(function () {
  'use strict';

  /**
   * Generates a stable unique CSS selector for an element
   */
  function getUniqueSelector(el) {
    if (el.id) {
      return `#${CSS.escape(el.id)}`;
    }
    if (el.name) {
      return `${el.tagName.toLowerCase()}[name="${CSS.escape(el.name)}"]`;
    }
    // Path traversal fallback
    const path = [];
    let current = el;
    while (current && current.nodeType === Node.ELEMENT_NODE && current !== document.body) {
      let selector = current.tagName.toLowerCase();
      if (current.className && typeof current.className === 'string') {
        const firstClass = current.className.trim().split(/\s+/)[0];
        if (firstClass) selector += `.${CSS.escape(firstClass)}`;
      }
      let sibling = current;
      let nth = 1;
      while ((sibling = sibling.previousElementSibling)) {
        if (sibling.tagName === current.tagName) nth++;
      }
      if (nth > 1) selector += `:nth-of-type(${nth})`;
      path.unshift(selector);
      current = current.parentElement;
    }
    return path.join(' > ');
  }

  /**
   * Checks whether an element is visible to the user
   */
  function isVisible(el) {
    if (!el || el.type === 'hidden') return false;
    const style = window.getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') {
      return false;
    }
    const rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  }

  /**
   * Extracts the best associated human-readable label for an input element
   */
  function getElementLabel(el) {
    // 1. Explicit <label for="id">
    if (el.id) {
      const labelEl = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (labelEl) return labelEl.innerText.trim();
    }

    // 2. Parent <label>
    const parentLabel = el.closest('label');
    if (parentLabel) {
      return parentLabel.innerText.trim();
    }

    // 3. aria-label or aria-labelledby
    if (el.getAttribute('aria-label')) {
      return el.getAttribute('aria-label').trim();
    }
    const labelledby = el.getAttribute('aria-labelledby');
    if (labelledby) {
      const lblEl = document.getElementById(labelledby);
      if (lblEl) return lblEl.innerText.trim();
    }

    // 4. Placeholder
    if (el.placeholder) {
      return el.placeholder.trim();
    }

    // 5. Preceding sibling label or text
    const prev = el.previousElementSibling;
    if (prev && (prev.tagName === 'LABEL' || prev.tagName === 'SPAN')) {
      return prev.innerText.trim();
    }

    return el.name || el.id || '';
  }

  /**
   * Captures the structured DOMSnapshot
   */
  function captureSnapshot() {
    const rawInputs = Array.from(document.querySelectorAll('input, select, textarea'));
    const visibleInputs = rawInputs.filter(isVisible);

    let fieldIndex = 1;
    const fields = visibleInputs.map(el => {
      // Assign or reuse ephemeral element key
      let key = el.dataset.agyKey;
      if (!key) {
        key = `field_${String(fieldIndex).padStart(2, '0')}`;
        el.dataset.agyKey = key;
        fieldIndex++;
      }

      const rect = el.getBoundingClientRect();
      let options = [];
      if (el.tagName.toLowerCase() === 'select') {
        options = Array.from(el.options).map(o => o.text.trim());
      }

      return {
        element_key: key,
        selector: getUniqueSelector(el),
        tag_name: el.tagName.toLowerCase(),
        input_type: el.type || 'text',
        label: getElementLabel(el),
        name: el.name || null,
        id: el.id || null,
        placeholder: el.placeholder || null,
        is_required: el.required || el.getAttribute('aria-required') === 'true',
        is_disabled: el.disabled || false,
        current_value: el.value || null,
        options: options,
        bounding_box: {
          x: Math.round(rect.x + window.scrollX),
          y: Math.round(rect.y + window.scrollY),
          width: Math.round(rect.width),
          height: Math.round(rect.height)
        }
      };
    });

    // Detect action buttons
    const rawButtons = Array.from(document.querySelectorAll('button, input[type="button"], input[type="submit"], a.btn'));
    const buttons = rawButtons.filter(isVisible).map((btn, idx) => ({
      element_key: `btn_${String(idx + 1).padStart(2, '0')}`,
      selector: getUniqueSelector(btn),
      text: (btn.innerText || btn.value || '').trim(),
      button_type: btn.type || 'button',
      is_primary: (btn.className || '').includes('primary') || (btn.className || '').includes('success'),
      is_disabled: btn.disabled || false
    }));

    // Detect step indicator
    let stepIndicator = null;
    let stepNum = 1;
    let totalSteps = 4;

    const stepHeading = document.querySelector('h1, h2, .step-title, [id*="step-heading"]');
    if (stepHeading && stepHeading.innerText.includes('Step')) {
      stepIndicator = stepHeading.innerText.trim();
      const match = stepIndicator.match(/Step\s*(\d+)\s*(?:of|\/)\s*(\d+)/i);
      if (match) {
        stepNum = parseInt(match[1], 10);
        totalSteps = parseInt(match[2], 10);
      }
    } else {
      const activeNav = document.querySelector('.step-item.active');
      if (activeNav) {
        stepIndicator = activeNav.innerText.trim();
      }
    }

    const snippet = (document.body.innerText || '').slice(0, 1000);

    return {
      url: window.location.href,
      title: document.title,
      step_indicator: stepIndicator,
      current_step_number: stepNum,
      total_steps: totalSteps,
      fields: fields,
      buttons: buttons,
      text_content_snippet: snippet,
      timestamp: new Date().toISOString()
    };
  }

  // Export to global scope and Chrome message listener
  window.DOMInspector = { captureSnapshot };

  if (typeof chrome !== 'undefined' && chrome.runtime && chrome.runtime.onMessage) {
    chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
      if (request.action === 'INSPECT_DOM') {
        const snapshot = captureSnapshot();
        sendResponse({ success: true, snapshot });
      }
      return true;
    });
  }

  console.log('[Universal AI Assistant] DOM Inspector Content Script Loaded.');
})();
