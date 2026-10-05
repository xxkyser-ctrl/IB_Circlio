if (typeof importScripts === "function") importScripts("config.js");
const ext = globalThis.browser ?? globalThis.chrome;
const extensionAction = ext.action ?? ext.browserAction;
const API_BASE = globalThis.INSTAGRAM_EXPORTER_API_BASE;
const API_TOKEN = globalThis.INSTAGRAM_EXPORTER_TOKEN;
const EXTENSION_VERSION = ext.runtime.getManifest().version;
const UPDATE_ALARM = "ib-circlio-release-check";
const RELEASES_URL =
  "https://api.github.com/repos/xxkyser-ctrl/IB_Circlio/releases/latest";
const UPDATE_INTERVAL_MS = 24 * 60 * 60 * 1000;
let collectionUiState = {
  active: false,
  status: "Ready to collect the current profile.",
  tabId: null
};

function extensionCall(target, method, ...args) {
  if (globalThis.browser) return Promise.resolve(target[method](...args));
  return new Promise((resolve, reject) => {
    target[method](...args, (result) => {
      const error = ext.runtime.lastError;
      if (error) reject(new Error(error.message));
      else resolve(result);
    });
  });
}

function parseStableVersion(version) {
  if (typeof version !== "string") return null;
  const match = /^v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:\+[0-9A-Za-z.-]+)?$/.exec(
    version.trim()
  );
  return match ? match.slice(1).map(Number) : null;
}

function isNewerVersion(candidate, current) {
  const next = parseStableVersion(candidate);
  const installed = parseStableVersion(current);
  if (!next || !installed) return false;
  for (let index = 0; index < 3; index += 1) {
    if (next[index] !== installed[index]) return next[index] > installed[index];
  }
  return false;
}

async function storedUpdateState() {
  return extensionCall(ext.storage.local, "get", [
    "checkForUpdates",
    "latestVersion",
    "releaseUrl",
    "releaseNotes",
    "lastChecked",
    "dismissedVersion"
  ]);
}

async function refreshUpdateBadge(state) {
  const visible = Boolean(
    state.latestVersion &&
    isNewerVersion(state.latestVersion, EXTENSION_VERSION) &&
    state.latestVersion !== state.dismissedVersion
  );
  await extensionCall(extensionAction, "setBadgeText", {
    text: visible ? "NEW" : ""
  });
  if (visible) {
    await extensionCall(extensionAction, "setBadgeBackgroundColor", {
      color: "#5265e8"
    });
  }
}

async function checkLatestRelease(force = false) {
  const stored = await storedUpdateState();
  if (stored.checkForUpdates === false) {
    await refreshUpdateBadge(stored);
    return;
  }
  const now = Date.now();
  if (!force && Number.isFinite(stored.lastChecked) &&
      now - stored.lastChecked < UPDATE_INTERVAL_MS) return;
  const updateState = { lastChecked: now };
  try {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 5000);
    let response;
    try {
      response = await fetch(RELEASES_URL, {
        headers: {
          Accept: "application/vnd.github+json",
          "User-Agent": "IB-Circlio-Update-Check"
        },
        signal: controller.signal
      });
    } finally {
      clearTimeout(timeout);
    }
    if (!response.ok) throw new Error(`GitHub returned HTTP ${response.status}`);
    const release = await response.json();
    const version = typeof release.tag_name === "string"
      ? release.tag_name.replace(/^v/, "")
      : "";
    const releaseUrl = typeof release.html_url === "string" &&
      release.html_url.startsWith(
        "https://github.com/xxkyser-ctrl/IB_Circlio/releases/"
      )
      ? release.html_url
      : "";
    const available = !release.draft && !release.prerelease &&
      releaseUrl && isNewerVersion(version, EXTENSION_VERSION);
    updateState.latestVersion = available ? version : "";
    updateState.releaseUrl = available ? releaseUrl : "";
    updateState.releaseNotes = available && typeof release.body === "string"
      ? release.body
      : "";
  } catch (error) {
    console.warn("IB Circlio release check failed:", error.message);
  }
  await extensionCall(ext.storage.local, "set", updateState);
  await refreshUpdateBadge({ ...stored, ...updateState });
}

async function configureUpdateChecks(enabled) {
  await extensionCall(ext.storage.local, "set", {
    checkForUpdates: Boolean(enabled)
  });
  if (enabled) {
    ext.alarms.create(UPDATE_ALARM, { periodInMinutes: 24 * 60 });
    await checkLatestRelease(true);
  } else {
    await extensionCall(ext.alarms, "clear", UPDATE_ALARM);
    await extensionCall(extensionAction, "setBadgeText", { text: "" });
  }
}

async function initializeUpdateChecks(force) {
  const stored = await storedUpdateState();
  if (stored.checkForUpdates === undefined) {
    await extensionCall(ext.storage.local, "set", { checkForUpdates: true });
  }
  if (stored.checkForUpdates === false) {
    await extensionCall(ext.alarms, "clear", UPDATE_ALARM);
    return;
  }
  ext.alarms.create(UPDATE_ALARM, { periodInMinutes: 24 * 60 });
  await checkLatestRelease(force);
}

if (!API_BASE || !API_TOKEN || API_TOKEN === "PASTE_LOCAL_SERVER_TOKEN_HERE") {
  throw new Error("Run run_server.bat first to generate the local configuration.");
}

async function apiRequest(path, method = "GET", body) {
  const response = await fetch(`${API_BASE}${path}`, {
    method,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${API_TOKEN}`
    },
    body: body === undefined ? undefined : JSON.stringify(body)
  }).catch(() => {
    throw new Error("Cannot reach the local database. Start run_server.bat and try again.");
  });
  let payload;
  try {
    payload = await response.json();
  } catch {
    throw new Error(`Local database returned an invalid response (${response.status}).`);
  }
  if (!response.ok || payload.ok === false) {
    throw new Error(payload.error || `Local database request failed (${response.status}).`);
  }
  return payload;
}

ext.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message.action === "getUpdateState") {
    storedUpdateState()
      .then(async (state) => {
        let serviceVersion = null;
        try {
          serviceVersion = (await apiRequest("/version")).version;
        } catch {
          // The desktop service is normally stopped until the user starts it.
        }
        sendResponse({
          ok: true,
          updates: state,
          versionMismatch: serviceVersion !== null &&
            serviceVersion !== EXTENSION_VERSION,
          serviceVersion,
          extensionVersion: EXTENSION_VERSION
        });
      })
      .catch((error) => sendResponse({ ok: false, error: error.message }));
    return true;
  }
  if (message.action === "setUpdateChecks") {
    configureUpdateChecks(message.enabled)
      .then(() => sendResponse({ ok: true }))
      .catch((error) => sendResponse({ ok: false, error: error.message }));
    return true;
  }
  if (message.action === "dismissUpdate") {
    extensionCall(ext.storage.local, "set", {
      dismissedVersion: message.version || ""
    })
      .then(async () => {
        await refreshUpdateBadge(await storedUpdateState());
        sendResponse({ ok: true });
      })
      .catch((error) => sendResponse({ ok: false, error: error.message }));
    return true;
  }
  if (message.action === "getCollectionState") {
    sendResponse({ ok: true, state: collectionUiState });
    return;
  }
  if (message.action === "collectionState" || message.action === "scanProgress" ||
      message.action === "collectionProgress") {
    const incoming = message.state || {};
    collectionUiState = {
      ...collectionUiState,
      ...incoming,
      active: message.action === "collectionState"
        ? Boolean(incoming.active)
        : true,
      status: incoming.status || message.message ||
        `Scanning ${message.listType}: ${message.count} collected`,
      tabId: _sender.tab?.id ?? collectionUiState.tabId
    };
    if (!collectionUiState.active) collectionUiState.tabId = null;
    sendResponse({ ok: true });
    return;
  }
  if (message.action === "stopCollection") {
    if (!collectionUiState.active || !Number.isInteger(collectionUiState.tabId)) {
      sendResponse({ ok: false, error: "No collection is currently running." });
      return;
    }
    extensionCall(ext.tabs, "sendMessage", collectionUiState.tabId, { action: "stop" })
      .then(() => sendResponse({ ok: true }))
      .catch((error) => sendResponse({ ok: false, error: error.message }));
    return true;
  }
  if (message.action === "saveCollection") {
    apiRequest("/api/collections", "POST", message.collection || {})
      .then((payload) => sendResponse(payload))
      .catch((error) => sendResponse({ ok: false, error: error.message }));
    return true;
  }
  if (message.action === "getHistory") {
    apiRequest(`/api/collections/history?profile=${encodeURIComponent(message.profile || "")}`)
      .then((payload) => sendResponse(payload))
      .catch((error) => sendResponse({ ok: false, error: error.message }));
    return true;
  }
  if (message.action === "clearHistory") {
    apiRequest(`/api/collections?profile=${encodeURIComponent(message.profile || "")}`, "DELETE")
      .then((payload) => sendResponse(payload))
      .catch((error) => sendResponse({ ok: false, error: error.message }));
    return true;
  }
});

ext.tabs.onRemoved.addListener((tabId) => {
  if (collectionUiState.active && collectionUiState.tabId === tabId) {
    collectionUiState = {
      active: false,
      status: "Collection stopped because its Instagram tab was closed.",
      tabId: null
    };
  }
});

ext.runtime.onInstalled.addListener(() => {
  initializeUpdateChecks(true).catch((error) =>
    console.warn("IB Circlio update setup failed:", error.message)
  );
});
ext.runtime.onStartup.addListener(() => {
  initializeUpdateChecks(false).catch((error) =>
    console.warn("IB Circlio update setup failed:", error.message)
  );
});
ext.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === UPDATE_ALARM) {
    checkLatestRelease(false).catch((error) =>
      console.warn("IB Circlio release check failed:", error.message)
    );
  }
});
