importScripts("config.js");
const API_BASE = globalThis.INSTAGRAM_EXPORTER_API_BASE;
const API_TOKEN = globalThis.INSTAGRAM_EXPORTER_TOKEN;
let collectionUiState = {
  active: false,
  status: "Ready to collect the current profile.",
  tabId: null
};

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

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
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
    chrome.tabs.sendMessage(collectionUiState.tabId, { action: "stop" })
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

chrome.tabs.onRemoved.addListener((tabId) => {
  if (collectionUiState.active && collectionUiState.tabId === tabId) {
    collectionUiState = {
      active: false,
      status: "Collection stopped because its Instagram tab was closed.",
      tabId: null
    };
  }
});
