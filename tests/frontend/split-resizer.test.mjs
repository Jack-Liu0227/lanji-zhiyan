import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const code = fs.readFileSync(new URL('../../apps/desktop/web/views/split-resizer.js', import.meta.url), 'utf8');

function createMockDOM() {
  const storage = new Map();
  const localStorage = {
    getItem: (k) => (storage.has(k) ? storage.get(k) : null),
    setItem: (k, v) => storage.set(k, String(v)),
    removeItem: (k) => storage.delete(k),
    clear: () => storage.clear()
  };

  class MockElement {
    constructor(tagName = 'div') {
      this.tagName = tagName.toUpperCase();
      this.nodeType = 1;
      this._className = '';
      this.classList = {
        _classes: new Set(),
        add: (...cls) => {
          cls.forEach(c => this.classList._classes.add(c));
          this._className = [...this.classList._classes].join(' ');
        },
        remove: (...cls) => {
          cls.forEach(c => this.classList._classes.delete(c));
          this._className = [...this.classList._classes].join(' ');
        },
        contains: (c) => this.classList._classes.has(c),
        toggle: (c, force) => {
          if (force !== undefined) {
            force ? this.classList.add(c) : this.classList.remove(c);
          } else {
            this.classList.contains(c) ? this.classList.remove(c) : this.classList.add(c);
          }
        }
      };
      this.attributes = new Map();
      this.dataset = {};
      this.style = {
        _vars: new Map(),
        setProperty: (k, v) => this.style._vars.set(k, v),
        getPropertyValue: (k) => this.style._vars.get(k) || '',
        left: '',
        right: ''
      };
      this.children = [];
      this.parentElement = null;
      this.eventListeners = new Map();
    }

    get className() { return this._className; }
    set className(val) {
      this._className = String(val || '');
      this.classList._classes = new Set(this._className.split(/\s+/).filter(Boolean));
    }

    setAttribute(name, val) { this.attributes.set(name, String(val)); }
    getAttribute(name) { return this.attributes.get(name) || null; }
    removeAttribute(name) { this.attributes.delete(name); }

    appendChild(child) {
      child.parentElement = this;
      this.children.push(child);
      return child;
    }

    insertBefore(child, ref) {
      child.parentElement = this;
      const idx = this.children.indexOf(ref);
      if (idx === -1) this.children.push(child);
      else this.children.splice(idx, 0, child);
      return child;
    }

    querySelector(sel) {
      if (sel.startsWith(':scope > .')) {
        const clsMatch = sel.match(/:scope > \.([a-zA-Z0-9_-]+)/);
        const attrMatch = sel.match(/\[([a-zA-Z0-9_-]+)="([^"]+)"\]/);
        const cls = clsMatch ? clsMatch[1] : '';
        return this.children.find(c => {
          if (cls && !c.classList.contains(cls)) return false;
          if (attrMatch) {
            const attrName = attrMatch[1];
            const attrVal = attrMatch[2];
            const datasetKey = attrName.startsWith('data-')
              ? attrName.slice(5).replace(/-([a-z])/g, (_, l) => l.toUpperCase())
              : null;
            if (datasetKey && c.dataset && c.dataset[datasetKey] !== attrVal) return false;
            if (!datasetKey && c.getAttribute(attrName) !== attrVal) return false;
          }
          return true;
        }) || null;
      }
      return null;
    }

    querySelectorAll(sel) {
      return this.children.filter(c => c.classList.contains(sel.replace('.', '')));
    }

    addEventListener(event, fn) {
      if (!this.eventListeners.has(event)) this.eventListeners.set(event, []);
      this.eventListeners.get(event).push(fn);
    }

    removeEventListener(event, fn) {
      if (!this.eventListeners.has(event)) return;
      const list = this.eventListeners.get(event).filter(f => f !== fn);
      this.eventListeners.set(event, list);
    }

    dispatchEvent(event) {
      const list = this.eventListeners.get(event.type) || [];
      list.forEach(fn => fn(event));
    }

    setPointerCapture() {}
    releasePointerCapture() {}
    hasPointerCapture() { return false; }
    getBoundingClientRect() { return { left: 0, top: 0, width: 1000, height: 600 }; }
    remove() {
      if (this.parentElement) {
        const idx = this.parentElement.children.indexOf(this);
        if (idx !== -1) this.parentElement.children.splice(idx, 1);
        this.parentElement = null;
      }
    }
  }

  const document = {
    createElement: (tag) => new MockElement(tag),
    body: new MockElement('body'),
    addEventListener: () => {}
  };

  const window = {
    localStorage,
    requestAnimationFrame: (cb) => cb(),
    cancelAnimationFrame: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    MutationObserver: class {
      observe() {}
      disconnect() {}
    }
  };

  const context = vm.createContext({
    window,
    document,
    localStorage,
    HTMLElement: MockElement,
    MutationObserver: window.MutationObserver,
    requestAnimationFrame: window.requestAnimationFrame,
    cancelAnimationFrame: window.cancelAnimationFrame,
    console
  });

  vm.runInContext(code, context);
  return { context, window, document, localStorage, MockElement };
}

test('SplitResizer binds correctly and initializes DOM gutter', () => {
  const { context, document, MockElement } = createMockDOM();
  const SplitResizer = context.window.SplitResizer;
  assert.ok(SplitResizer, 'SplitResizer should be defined on window');

  const container = new MockElement('div');
  const instance = SplitResizer.bind(container, {
    mode: 'fixed-left',
    storageKey: 'test-layout',
    defaultSize: 240,
    min: 150,
    max: 400,
    cssVar: '--test-w'
  });

  assert.ok(instance, 'Instance should be created');
  assert.equal(container.style.getPropertyValue('--test-w'), '240px');
  assert.equal(container.children.length, 1);

  const gutter = container.children[0];
  assert.ok(gutter.classList.contains('split-gutter'));
  assert.ok(gutter.classList.contains('split-gutter-absolute'));
  assert.equal(gutter.getAttribute('role'), 'separator');
  assert.equal(gutter.getAttribute('aria-valuenow'), '240');
  assert.equal(gutter.getAttribute('aria-valuemin'), '150');
  assert.equal(gutter.getAttribute('aria-valuemax'), '400');
  assert.equal(gutter.style.left, '240px');
});

test('SplitResizer clamps values within [min, max]', () => {
  const { context, MockElement } = createMockDOM();
  const SplitResizer = context.window.SplitResizer;

  const container = new MockElement('div');
  const instance = SplitResizer.bind(container, {
    mode: 'fixed-left',
    defaultSize: 500,
    min: 150,
    max: 300,
    cssVar: '--test-w'
  });

  assert.equal(instance.getSize(), 300, 'Size should be clamped to max');
  instance.setSize(100);
  assert.equal(instance.getSize(), 150, 'Size should be clamped to min');
});

test('SplitResizer persists and restores size from localStorage', () => {
  const { context, localStorage, MockElement } = createMockDOM();
  const SplitResizer = context.window.SplitResizer;

  localStorage.setItem('rw-split:cached-layout', '290');

  const container = new MockElement('div');
  const instance = SplitResizer.bind(container, {
    mode: 'fixed-left',
    storageKey: 'cached-layout',
    defaultSize: 220,
    min: 160,
    max: 400,
    cssVar: '--cached-w'
  });

  assert.equal(instance.getSize(), 290, 'Size should be loaded from localStorage');
  assert.equal(container.style.getPropertyValue('--cached-w'), '290px');
});

test('SplitResizer double click resets to defaultSize', () => {
  const { context, localStorage, MockElement } = createMockDOM();
  const SplitResizer = context.window.SplitResizer;

  const container = new MockElement('div');
  const instance = SplitResizer.bind(container, {
    mode: 'fixed-left',
    storageKey: 'reset-layout',
    defaultSize: 224,
    min: 160,
    max: 400,
    cssVar: '--reset-w'
  });

  instance.setSize(380);
  assert.equal(instance.getSize(), 380);
  assert.equal(localStorage.getItem('rw-split:reset-layout'), '380');

  // Trigger dblclick on gutter
  instance.gutter.dispatchEvent({ type: 'dblclick', preventDefault: () => {}, stopPropagation: () => {} });
  assert.equal(instance.getSize(), 224, 'Size should reset to defaultSize after dblclick');
  assert.equal(localStorage.getItem('rw-split:reset-layout'), '224');
});

test('SplitResizer keyboard arrow keys adjust step', () => {
  const { context, MockElement } = createMockDOM();
  const SplitResizer = context.window.SplitResizer;

  const container = new MockElement('div');
  const instance = SplitResizer.bind(container, {
    mode: 'fixed-left',
    defaultSize: 200,
    min: 100,
    max: 400,
    cssVar: '--step-w'
  });

  // Right arrow adds step (12px)
  instance.gutter.dispatchEvent({ type: 'keydown', key: 'ArrowRight', preventDefault: () => {} });
  assert.equal(instance.getSize(), 212);

  // Left arrow subtracts step (12px)
  instance.gutter.dispatchEvent({ type: 'keydown', key: 'ArrowLeft', preventDefault: () => {} });
  assert.equal(instance.getSize(), 200);

  // Home key goes to min
  instance.gutter.dispatchEvent({ type: 'keydown', key: 'Home', preventDefault: () => {} });
  assert.equal(instance.getSize(), 100);

  // End key goes to max
  instance.gutter.dispatchEvent({ type: 'keydown', key: 'End', preventDefault: () => {} });
  assert.equal(instance.getSize(), 400);
});

test('SplitResizer ratio mode formats percentage correctly', () => {
  const { context, MockElement } = createMockDOM();
  const SplitResizer = context.window.SplitResizer;

  const container = new MockElement('div');
  const instance = SplitResizer.bind(container, {
    mode: 'ratio',
    storageKey: 'ratio-layout',
    defaultSize: 0.5,
    min: 0.2,
    max: 0.8,
    cssVar: '--ratio-w'
  });

  assert.equal(container.style.getPropertyValue('--ratio-w'), '50.00%');
  instance.setSize(0.65);
  assert.equal(container.style.getPropertyValue('--ratio-w'), '65.00%');
});

test('SplitResizer supports multiple resizers on single container (e.g. wnw-grid)', () => {
  const { context, MockElement } = createMockDOM();
  const SplitResizer = context.window.SplitResizer;

  const container = new MockElement('div');
  const leftInstance = SplitResizer.bind(container, {
    mode: 'fixed-left',
    storageKey: 'multi-left',
    defaultSize: 220,
    cssVar: '--wnw-context-w'
  });
  const rightInstance = SplitResizer.bind(container, {
    mode: 'fixed-right',
    storageKey: 'multi-right',
    defaultSize: 300,
    cssVar: '--wnw-drawer-w'
  });

  assert.equal(container.children.length, 2, 'Should create 2 gutters');
  assert.equal(container.style.getPropertyValue('--wnw-context-w'), '220px');
  assert.equal(container.style.getPropertyValue('--wnw-drawer-w'), '300px');
  assert.equal(SplitResizer.get(container, '--wnw-context-w'), leftInstance);
  assert.equal(SplitResizer.get(container, '--wnw-drawer-w'), rightInstance);
});

test('SplitResizer reuses explicit gutter element and positioning flow', () => {
  const { context, MockElement } = createMockDOM();
  const SplitResizer = context.window.SplitResizer;

  const container = new MockElement('div');
  const customDivider = new MockElement('div');
  customDivider.className = 'fl-split-divider';
  container.appendChild(customDivider);

  const instance = SplitResizer.bind(container, {
    mode: 'ratio',
    storageKey: 'custom-divider-test',
    defaultSize: 0.5,
    cssVar: '--custom-ratio',
    gutter: customDivider,
    positioning: 'flow'
  });

  assert.equal(container.children.length, 1, 'Should NOT append extra gutter');
  assert.equal(instance.gutter, customDivider);
  assert.ok(customDivider.classList.contains('split-gutter'));
  assert.equal(container.style.getPropertyValue('--custom-ratio'), '50.00%');
});
