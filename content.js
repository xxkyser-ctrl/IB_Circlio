(() => {
  const state = {
    stopRequested: false,
    running: false,
    status: "Ready to collect the current profile."
  };
  const excludedPaths = new Set([
    "accounts", "direct", "explore", "reel", "reels", "stories", "p", "tv", "create"
  ]);
  const sleep = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

  function usernameFromHref(href) {
    if (!href) return null;
    let path;
    try {
      path = new URL(href, location.origin).pathname;
    } catch {
      return null;
    }
    const match = path.match(/^\/([a-zA-Z0-9._]{1,30})\/?$/);
    if (!match || excludedPaths.has(match[1].toLowerCase())) return null;
    return match[1].toLowerCase();
  }

  function findDialog() {
    const dialog = document.querySelector('div[role="dialog"]');
    return dialog;
  }

  function findScrollContainer(dialog) {
    const candidates = [dialog, ...dialog.querySelectorAll("div")];
    const scrollable = candidates.filter((element) => {
      const style = getComputedStyle(element);
      return element.scrollHeight > element.clientHeight + 10 &&
        element.clientHeight > 0 &&
        (style.overflowY === "auto" || style.overflowY === "scroll" ||
          style.overflowY === "hidden" || element === dialog);
    });
    const selected = scrollable.sort((left, right) =>
      (right.scrollHeight - right.clientHeight) - (left.scrollHeight - left.clientHeight)
    )[0] || dialog;
    return selected;
  }

  function extractUsernames(dialog, users, avatars) {
    let added = 0;
    for (const link of dialog.querySelectorAll("a[href]")) {
      const username = usernameFromHref(link.getAttribute("href"));
      if (username && !users.has(username)) {
        users.add(username);
        added += 1;
      }
      const row = link.closest('[role="listitem"], li') ||
        link.parentElement?.parentElement;
      const images = [
        ...link.querySelectorAll("img[src]"),
        ...(row ? row.querySelectorAll("img[src]") : [])
      ];
      const image = images.find((candidate) =>
        candidate.src &&
        (!candidate.alt || candidate.alt.toLowerCase().includes(username))
      ) || images.find((candidate) => candidate.src);
      if (username && image && image.src && !avatars[username]) avatars[username] = image.src;
    }
    return added;
  }

  async function scanDialog(listType, avatars) {
    const dialog = findDialog();
    if (!dialog) throw new Error(`${listType} dialog not detected.`);
    const users = new Set();
    let stableAttempts = 0;
    let attempts = 0;

    while (!state.stopRequested && attempts < 400) {
      const liveDialog = findDialog();
      if (!liveDialog) throw new Error(`${listType} dialog closed during scan.`);
      const container = findScrollContainer(liveDialog);
      const addedBeforeScroll = extractUsernames(liveDialog, users, avatars);
      const previousTop = container.scrollTop;
      const step = Math.max(240, Math.floor(Math.max(container.clientHeight, 300) * 0.85));
      container.scrollTop = Math.min(container.scrollTop + step, container.scrollHeight);
      container.dispatchEvent(new WheelEvent("wheel", {
        bubbles: true,
        cancelable: true,
        deltaY: step
      }));
      await sleep(900);
      const refreshedDialog = findDialog();
      if (!refreshedDialog) throw new Error(`${listType} dialog closed during scan.`);
      const refreshedContainer = findScrollContainer(refreshedDialog);
      const addedAfterScroll = extractUsernames(refreshedDialog, users, avatars);
      const added = addedBeforeScroll + addedAfterScroll;
      attempts += 1;
      sendCollectionState(true, `Scanning ${listType}: ${users.size} usernames found`, {
        listType, count: users.size, attempts
      });

      if (added === 0) stableAttempts += 1;
      else stableAttempts = 0;
      const moved = refreshedContainer.scrollTop > previousTop + 2;
      const atBottom = refreshedContainer.scrollTop + refreshedContainer.clientHeight >= refreshedContainer.scrollHeight - 10;
      if (!moved && added === 0) {
        const visibleLinks = [...refreshedDialog.querySelectorAll("a[href]")].filter((link) => link.offsetParent !== null);
        visibleLinks[visibleLinks.length - 1]?.scrollIntoView({ block: "end" });
        await sleep(500);
      }
      if (stableAttempts >= 4 && atBottom) break;
    }
    if (attempts >= 400 && !state.stopRequested) {
      throw new Error(`${listType} scan reached the safety limit; this snapshot will be saved as partial.`);
    }
    return [...users].sort();
  }

  async function closeDialog() {
    for (let attempt = 0; attempt < 3; attempt += 1) {
      const dialog = findDialog();
      if (!dialog) return;
      const dialogRect = dialog.getBoundingClientRect();
      const candidates = [...dialog.querySelectorAll(
        'button, [role="button"], [aria-label], img[alt], svg'
      )].filter((element) => {
        const rect = element.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0 && rect.top <= dialogRect.top + 90 &&
          rect.right >= dialogRect.right - 90;
      });
      const closeTarget = [...candidates, ...dialog.querySelectorAll(
        'button, [role="button"], [aria-label], img[alt]'
      )].find((element) => {
        const label = `${element.getAttribute("aria-label") || ""} ${element.getAttribute("alt") || ""}`
          .toLowerCase();
        return label.includes("close") || label.includes("fermer") ||
          label.includes("cerrar") || label.includes("chiudi");
      });
      const closeButton = closeTarget?.closest("button, [role=\"button\"]") ||
        closeTarget || candidates[0];
      if (closeButton instanceof HTMLElement) {
        closeButton.click();
        closeButton.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
        closeButton.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
      }
      for (const target of [dialog, document]) {
        target.dispatchEvent(new KeyboardEvent("keydown", {
          key: "Escape", code: "Escape", keyCode: 27, which: 27,
          bubbles: true, cancelable: true
        }));
      }
      await sleep(400);
      if (!findDialog()) return;
    }
    throw new Error("The previous Instagram list dialog did not close.");
  }

  function profileLink(listType) {
    const suffix = listType === "followers" ? "/followers" : "/following";
    const link = [...document.querySelectorAll("a[href]")]
      .filter((link) => {
        try {
          return new URL(link.href).pathname.replace(/\/$/, "").endsWith(suffix);
        } catch {
          return false;
        }
      })
      .find((link) => link.offsetParent !== null);
    if (link) return link;

    const labels = listType === "followers"
      ? ["followers", "abonnés", "seguidores", "seguidores"]
      : ["following", "suivi", "abonnements", "seguidos", "seguidos"];
    const candidates = [...document.querySelectorAll('button, [role="button"], a, li')]
      .filter((element) => element.offsetParent !== null);
    const match = candidates.find((element) => {
      const text = (element.textContent || "").trim().toLowerCase();
      return labels.some((label) => text.includes(label)) &&
        /\d/.test(text);
    });
    return match?.querySelector("a, button, [role=\"button\"]") || match;
  }

  function headerTotal(listType) {
    const link = profileLink(listType);
    if (!link) return null;
    const text = [
      link.getAttribute("aria-label"),
      link.getAttribute("title"),
      link.textContent
    ].filter(Boolean).join(" ").replace(/\s+/g, " ").trim().toLowerCase()
      .normalize("NFD").replace(/[\u0300-\u036f]/g, "");
    const match = text.match(/(\d[\d.,\s]*[kmb]?)\s*([a-z()]+)/i);
    if (!match) return null;
    const label = match[2];
    const isExpectedLabel = listType === "followers"
      ? label.includes("follower") || label.includes("abonn") || label.includes("seguidor")
      : label.includes("following") || label.includes("suivi") ||
        label.includes("abonn") || label.includes("seguid");
    if (!isExpectedLabel) return null;

    const raw = match[1].replace(/\s/g, "");
    const suffix = raw.slice(-1).toLowerCase();
    const multiplier = suffix === "k" ? 1000 : suffix === "m" ? 1000000 :
      suffix === "b" ? 1000000000 : 1;
    let numeric = multiplier === 1 ? raw : raw.slice(0, -1);
    if (numeric.includes(",") && numeric.includes(".")) {
      numeric = numeric.lastIndexOf(",") > numeric.lastIndexOf(".")
        ? numeric.replace(/\./g, "").replace(",", ".")
        : numeric.replace(/,/g, "");
    } else if (multiplier !== 1) {
      numeric = numeric.replace(",", ".");
    } else {
      numeric = numeric.replace(/[.,]/g, "");
    }
    const parsed = Number(numeric);
    const total = Number.isFinite(parsed) ? Math.round(parsed * multiplier) : null;
    if (listType === "following" && total !== null && total > 7500) return null;
    return total;
  }

  function headerTotalLabel(listType) {
    const link = profileLink(listType);
    if (!link) return null;
    return [
      link.getAttribute("aria-label"),
      link.getAttribute("title"),
      link.textContent
    ].filter(Boolean).join(" | ").replace(/\s+/g, " ").trim().slice(0, 300);
  }

  async function openList(listType) {
    if (findDialog()) await closeDialog();
    const link = profileLink(listType);
    if (!link) throw new Error(`Could not find the ${listType} button on this profile.`);
    link.click();
    for (let attempt = 0; attempt < 30; attempt += 1) {
      await sleep(250);
      if (findDialog()) {
        await sleep(500);
        return;
      }
    }
    throw new Error(`The ${listType} dialog did not open. Click the profile's ${listType} count once, then try again.`);
  }

  async function collectProfile() {
    if (state.running) throw new Error("A collection is already in progress in this tab.");
    if (!/^\/[^/]+\/?$/.test(location.pathname)) {
      throw new Error("Open an Instagram profile before starting a collection.");
    }
    state.running = true;
    state.stopRequested = false;
    sendCollectionState(true, "Starting collection...");
    const result = {
      followers: [],
      following: [],
      avatars: {},
      headerTotals: {
        followers: headerTotal("followers"),
        following: headerTotal("following")
      },
      headerTotalLabels: {
        followers: headerTotalLabel("followers"),
        following: headerTotalLabel("following")
      }
    };
    try {
      for (const listType of ["followers", "following"]) {
        if (state.stopRequested) break;
        sendCollectionState(true, `Opening ${listType}...`);
        await openList(listType);
        result[listType] = await scanDialog(listType, result.avatars);
        await closeDialog();
        await sleep(500);
      }
    } catch (error) {
      result.complete = false;
      result.error = error.message;
      if (findDialog()) {
        try {
          await closeDialog();
        } catch {
          // Keep the partial result even if Instagram leaves the dialog mounted.
        }
      }
    }
    if (state.stopRequested) result.complete = false;
    if (result.error) result.complete = false;

    try {
      const saveResponse = await new Promise((resolve, reject) => {
        chrome.runtime.sendMessage({
          action: "saveCollection",
          collection: {
            profile: location.pathname.replace(/^\/|\/$/g, ""),
            followers: result.followers,
            following: result.following,
            avatars: result.avatars,
            headerTotals: result.headerTotals,
            headerTotalLabels: result.headerTotalLabels,
            complete: result.complete
          }
        }, (response) => {
          if (chrome.runtime.lastError) {
            reject(new Error(chrome.runtime.lastError.message));
          } else if (!response?.ok) {
            reject(new Error(response?.error || "Could not save the collection."));
          } else {
            resolve(response);
          }
        });
      });
      const collection = saveResponse.collection;
      const status = result.error
        ? `Partial snapshot saved: ${result.error}`
        : collection.complete
          ? `Saved ${collection.followers.length} followers and ${collection.following.length} following.`
          : "Collection stopped. Partial snapshot saved.";
      sendCollectionState(false, status);
      return {
        ...result,
        collection,
        stopped: !result.complete,
        error: result.error || null
      };
    } catch (error) {
      sendCollectionState(false, `Collection failed: ${error.message}`);
      throw error;
    } finally {
      state.running = false;
    }
  }

  function sendCollectionState(active, status, details = {}) {
    state.running = active;
    state.status = status;
    chrome.runtime.sendMessage({
      action: "collectionState",
      state: { active, status, ...details }
    }).catch(() => {});
  }

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message.action === "getCollectionState") {
      sendResponse({
        ok: true,
        state: { active: state.running, status: state.status }
      });
      return;
    }
    if (message.action === "startCollection") {
      if (state.running) {
        sendResponse({ ok: false, error: "A collection is already in progress in this tab." });
        return;
      }
      collectProfile()
        .then((result) => sendResponse({ ok: true, ...result }))
        .catch((error) => sendResponse({ ok: false, error: error.message }));
      return true;
    }
    if (message.action === "scan") {
      scanDialog(message.listType)
        .then((users) => {
          sendResponse({ ok: true, users });
        })
        .catch((error) => sendResponse({ ok: false, error: error.message }));
      return true;
    }
    if (message.action === "stop") {
      state.stopRequested = true;
      sendResponse({ ok: true });
    }
  });
})();
