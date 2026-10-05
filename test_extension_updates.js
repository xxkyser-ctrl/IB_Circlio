const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const root = __dirname;
const backgroundSource = fs.readFileSync(
  path.join(root, "background.js"),
  "utf8"
);
const popupSource = fs.readFileSync(path.join(root, "popup.js"), "utf8");
const latestReleaseUrl =
  "https://api.github.com/repos/xxkyser-ctrl/IB_Circlio/releases/latest";

function makeEvent() {
  const listeners = [];
  return {
    addListener(listener) {
      listeners.push(listener);
    },
    async fire(...args) {
      await Promise.all(listeners.map((listener) => listener(...args)));
    },
    listeners
  };
}

function createExtension(family, initialRelease, networkError = null) {
  const storage = { checkForUpdates: true };
  const alarms = new Map();
  const runtimeMessages = makeEvent();
  const installed = makeEvent();
  const startup = makeEvent();
  const alarmEvents = makeEvent();
  const removedTabs = makeEvent();
  const popupMessages = makeEvent();
  const action = {
    badge: "",
    async setBadgeText({ text }, callback) {
      this.badge = text;
      if (callback) callback();
    },
    async setBadgeBackgroundColor(_details, callback) {
      if (callback) callback();
    }
  };
  const counters = { releaseFetches: 0, warnings: [] };
  let release = initialRelease;

  function respond(value, callback) {
    if (family === "firefox") return Promise.resolve(value);
    callback(value);
    return undefined;
  }

  function sendRuntimeMessage(message, callback) {
    return new Promise((resolve) => {
      const listener = runtimeMessages.listeners[0];
      if (!listener) {
        resolve(undefined);
        return;
      }
      let answered = false;
      const sendResponse = (response) => {
        if (!answered) {
          answered = true;
          resolve(response);
        }
      };
      const asynchronous = listener(message, {}, sendResponse);
      if (asynchronous !== true && !answered) sendResponse(undefined);
    }).then((value) => {
      if (callback) callback(value);
      return value;
    });
  }

  const runtime = {
    lastError: null,
    getManifest: () => ({ version: "1.0.8" }),
    onMessage: runtimeMessages,
    onInstalled: installed,
    onStartup: startup,
    sendMessage: sendRuntimeMessage
  };
  const localStorage = {
    get(keys, callback) {
      const result = Object.fromEntries(
        keys.filter((key) => key in storage).map((key) => [key, storage[key]])
      );
      return respond(result, callback);
    },
    set(values, callback) {
      Object.assign(storage, values);
      return respond(undefined, callback);
    }
  };
  const alarmApi = {
    create(name, options) {
      alarms.set(name, options);
    },
    clear(name, callback) {
      const cleared = alarms.delete(name);
      return respond(cleared, callback);
    },
    onAlarm: alarmEvents
  };
  const tabs = {
    query(_query, callback) {
      return respond(
        [{ id: 5, url: "https://www.instagram.com/example/" }],
        callback
      );
    },
    sendMessage(_tabId, _message, callback) {
      return respond(
        { ok: true, state: { active: false, status: "Ready" } },
        callback
      );
    },
    create(_details, callback) {
      return respond(undefined, callback);
    },
    onRemoved: removedTabs
  };
  const scripting = {
    executeScript(_details, callback) {
      return respond(undefined, callback);
    }
  };
  const browserApi = {
    runtime,
    storage: { local: localStorage },
    alarms: alarmApi,
    action,
    tabs,
    scripting
  };
  const chromeApi = {
    ...browserApi,
    browserAction: action
  };
  if (family === "chrome") chromeApi.action = action;
  else delete chromeApi.action;

  const sandbox = {
    INSTAGRAM_EXPORTER_API_BASE: "http://127.0.0.1:8765",
    INSTAGRAM_EXPORTER_TOKEN: "test-token-that-is-at-least-thirty-two-chars",
    console: {
      warn(...args) {
        counters.warnings.push(args);
      }
    },
    fetch: async (url) => {
      if (url === latestReleaseUrl) {
        counters.releaseFetches += 1;
        if (networkError) throw networkError;
        return {
          ok: true,
          json: async () => {
            if (release instanceof Error) throw release;
            return release;
          }
        };
      }
      if (url === "http://127.0.0.1:8765/version") {
        return { ok: true, json: async () => ({ ok: true, version: "1.0.8" }) };
      }
      throw new Error(`Unexpected fetch URL: ${url}`);
    },
    setInterval() {},
    setTimeout,
    clearTimeout,
    AbortController
  };
  if (family === "firefox") sandbox.browser = browserApi;
  else sandbox.chrome = chromeApi;
  const context = vm.createContext(sandbox);
  vm.runInContext(backgroundSource, context, { filename: "background.js" });

  const elements = new Map();
  function element(id) {
    if (!elements.has(id)) {
      elements.set(id, {
        hidden: id === "update-banner",
        checked: id === "check-updates",
        disabled: false,
        textContent: "",
        listeners: {},
        addEventListener(name, callback) {
          this.listeners[name] = callback;
        }
      });
    }
    return elements.get(id);
  }
  const popupContext = vm.createContext({
    ...sandbox,
    document: { getElementById: element },
    window: { setInterval() {} }
  });

  return {
    alarms,
    action,
    counters,
    elements,
    runtimeMessages,
    setRelease(value) {
      release = value;
    },
    async installPopup() {
      vm.runInContext(popupSource, popupContext, { filename: "popup.js" });
      await new Promise((resolve) => setImmediate(resolve));
    },
    async fireInstalled() {
      await installed.fire();
      await new Promise((resolve) => setImmediate(resolve));
    },
    async sendMessage(message) {
      return runtime.sendMessage(message);
    }
  };
}

function release(version) {
  return {
    tag_name: `v${version}`,
    html_url: `https://github.com/xxkyser-ctrl/IB_Circlio/releases/tag/v${version}`,
    body: `Notes for ${version}`,
    draft: false,
    prerelease: false
  };
}

for (const family of ["chrome", "firefox"]) {
  test(`${family}: newer release shows badge and popup banner; dismissal and toggle work`, async () => {
    const extension = createExtension(family, release("1.0.9"));
    await extension.fireInstalled();
    await extension.installPopup();

    assert.equal(extension.action.badge, "NEW");
    assert.equal(extension.elements.get("update-banner").hidden, false);
    assert.equal(
      extension.elements.get("update-heading").textContent,
      "Update available: v1.0.9"
    );

    await extension.elements.get("dismiss-update").listeners.click();
    assert.equal(extension.elements.get("update-banner").hidden, true);
    assert.equal(extension.action.badge, "");

    const checksBeforeDisable = extension.counters.releaseFetches;
    await extension.elements.get("check-updates").listeners.change.call(
      Object.assign(extension.elements.get("check-updates"), { checked: false })
    );
    assert.equal(extension.alarms.size, 0);
    assert.equal(extension.counters.releaseFetches, checksBeforeDisable);

    extension.setRelease(release("1.0.9"));
    await extension.elements.get("check-updates").listeners.change.call(
      Object.assign(extension.elements.get("check-updates"), { checked: true })
    );
    assert.equal(extension.action.badge, "NEW");
    assert.equal(extension.elements.get("update-banner").hidden, false);
    assert.equal(
      extension.elements.get("update-heading").textContent,
      "Update available: v1.0.9"
    );

    const checksBeforeSecondDisable = extension.counters.releaseFetches;
    await extension.elements.get("check-updates").listeners.change.call(
      Object.assign(extension.elements.get("check-updates"), { checked: false })
    );
    assert.equal(extension.alarms.size, 0);
    assert.equal(extension.counters.releaseFetches, checksBeforeSecondDisable);
    assert.equal(extension.elements.get("update-banner").hidden, true);
  });

  for (const version of ["1.0.7", "1.0.6"]) {
    test(`${family}: same or older release shows no update`, async () => {
      const extension = createExtension(family, release(version));
      await extension.fireInstalled();
      await extension.installPopup();
      assert.equal(extension.action.badge, "");
      assert.equal(extension.elements.get("update-banner").hidden, true);
    });
  }

  for (const failure of [
    { message: "network unavailable", networkError: new Error("network unavailable") },
    { message: "malformed release JSON", payload: new SyntaxError("malformed release JSON") }
  ]) {
    test(`${family}: ${failure.message} does not crash or show an update`, async () => {
      const extension = createExtension(
        family,
        failure.payload || null,
        failure.networkError || null
      );
      await extension.fireInstalled();
      await extension.installPopup();
      assert.equal(extension.action.badge, "");
      assert.equal(extension.elements.get("update-banner").hidden, true);
      assert.ok(extension.counters.warnings.length > 0);
    });
  }
}
