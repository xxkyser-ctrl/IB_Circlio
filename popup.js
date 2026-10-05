const ext = globalThis.browser ?? globalThis.chrome;
const statusElement = document.getElementById("status");
const startButton = document.getElementById("start");
const updateBanner = document.getElementById("update-banner");
const updatesCheckbox = document.getElementById("check-updates");
let availableReleaseVersion = "";
let availableReleaseUrl = "";

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

function sendRuntimeMessage(message) {
  return extensionCall(ext.runtime, "sendMessage", message);
}

function setStatus(message) {
  statusElement.textContent = message;
}

async function getActiveInstagramTab() {
  const tabs = await extensionCall(ext.tabs, "query", {
    active: true,
    currentWindow: true
  });
  const tab = tabs[0];
  if (!tab || !tab.url || !tab.url.startsWith("https://www.instagram.com/")) {
    throw new Error("Open an Instagram profile first.");
  }
  return tab;
}

async function sendToTab(tabId, message) {
  try {
    return await extensionCall(ext.tabs, "sendMessage", tabId, message);
  } catch (error) {
    if (!/Receiving end does not exist|Could not establish connection/i.test(error.message)) {
      throw error;
    }
    if (ext.scripting?.executeScript) {
      await extensionCall(ext.scripting, "executeScript", {
        target: { tabId },
        files: ["content.js"]
      });
    } else if (ext.tabs.executeScript) {
      await extensionCall(ext.tabs, "executeScript", tabId, {
        file: "content.js"
      });
    } else {
      throw new Error("This browser cannot inject the collection script.");
    }
    return extensionCall(ext.tabs, "sendMessage", tabId, message);
  }
}

function setBusy(isBusy) {
  startButton.disabled = isBusy;
  document.getElementById("stop").disabled = !isBusy;
}

async function refreshCollectionState() {
  try {
    const tab = await getActiveInstagramTab();
    const response = await sendToTab(tab.id, { action: "getCollectionState" });
    if (!response?.ok) throw new Error(response?.error || "Could not read collection status.");
    setBusy(Boolean(response.state.active));
    setStatus(response.state.status || "Ready to collect the current profile.");
  } catch (error) {
    setBusy(false);
    setStatus(`Status unavailable: ${error.message}`);
  }
}

async function refreshUpdateState() {
  try {
    const response = await sendRuntimeMessage({ action: "getUpdateState" });
    if (!response?.ok) return;
    updatesCheckbox.checked = response.updates.checkForUpdates !== false;
    document.getElementById("version-mismatch").hidden =
      !response.versionMismatch;
    const release = response.updates;
    if (release.checkForUpdates !== false &&
        release.latestVersion &&
        release.latestVersion !== release.dismissedVersion &&
        release.releaseUrl) {
      availableReleaseVersion = release.latestVersion;
      availableReleaseUrl = release.releaseUrl;
      document.getElementById("update-heading").textContent =
        `Update available: v${release.latestVersion}`;
      document.getElementById("release-notes").textContent =
        release.releaseNotes || "No release notes were provided.";
      updateBanner.hidden = false;
    } else {
      updateBanner.hidden = true;
    }
  } catch {
    // Update metadata is optional; collection remains available if the service is stopped.
  }
}

async function startCollection() {
  if (startButton.disabled) return;
  setBusy(true);
  let collectionError = null;
  try {
    const tab = await getActiveInstagramTab();
    setStatus("Connecting to the profile...");
    const response = await sendToTab(tab.id, { action: "startCollection" });
    if (!response?.ok) throw new Error(response?.error || "Collection failed.");
    if (response.stopped) {
      setStatus(response.error
        ? `Partial collection saved. ${response.error}`
        : "Collection stopped. The collected data was saved as a partial snapshot.");
      return;
    }
    const collection = response.collection;
    setStatus(collection.complete
      ? `Saved ${collection.followers.length} followers and ${collection.following.length} following.`
      : "Collection stopped. The collected data was saved as a partial snapshot.");
  } catch (error) {
    collectionError = error.message;
  } finally {
    await refreshCollectionState();
    if (collectionError) setStatus(`Collection error: ${collectionError}`);
  }
}

startButton.addEventListener("click", startCollection);
document.getElementById("stop").addEventListener("click", async () => {
  try {
    const tab = await getActiveInstagramTab();
    const response = await sendToTab(tab.id, { action: "stop" });
    if (!response?.ok) throw new Error(response?.error || "Could not stop the collection.");
    setStatus("Stopping collection...");
  } catch (error) {
    setStatus(error.message);
  }
});
document.getElementById("feedback").addEventListener("click", () => {
  extensionCall(ext.tabs, "create", {
    url: "https://github.com/xxkyser-ctrl/IB_Circlio/issues/new"
  }).catch((error) => setStatus(error.message));
});
document.getElementById("open-release").addEventListener("click", () => {
  if (availableReleaseUrl) {
    extensionCall(ext.tabs, "create", { url: availableReleaseUrl })
      .catch((error) => setStatus(error.message));
  }
});
document.getElementById("dismiss-update").addEventListener("click", async () => {
  try {
    await sendRuntimeMessage({
      action: "dismissUpdate",
      version: availableReleaseVersion
    });
    updateBanner.hidden = true;
  } catch (error) {
    setStatus(error.message);
  }
});
updatesCheckbox.addEventListener("change", async () => {
  try {
    const response = await sendRuntimeMessage({
      action: "setUpdateChecks",
      enabled: updatesCheckbox.checked
    });
    if (!response?.ok) throw new Error(response?.error || "Could not save update preference.");
    await refreshUpdateState();
  } catch (error) {
    setStatus(error.message);
    updatesCheckbox.checked = !updatesCheckbox.checked;
  }
});
ext.runtime.onMessage.addListener((message) => {
  if (message.action === "scanProgress" || message.action === "collectionProgress") {
    setStatus(message.message || `Scanning ${message.listType}: ${message.count} collected`);
  }
  if (message.action === "collectionState" || message.action === "scanProgress" ||
      message.action === "collectionProgress") {
    refreshCollectionState();
  }
});

setBusy(true);
refreshCollectionState();
refreshUpdateState();
window.setInterval(refreshCollectionState, 700);
