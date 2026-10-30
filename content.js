const ext = globalThis.browser ?? globalThis.chrome;

(() => {
  const state = {
    stopRequested: false,
    running: false,
    status: "Ready to collect the current profile."
  };
  const excludedPaths = new Set([
    "accounts", "direct", "explore", "reel", "reels", "stories", "p", "tv", "create"
  ]);
  const NO_NEW_USER_WAIT_MS = 5000;
  const sleep = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

  function sendRuntimeMessage(message) {
    if (globalThis.browser) return ext.runtime.sendMessage(message);
    return new Promise((resolve, reject) => {
      ext.runtime.sendMessage(message, (response) => {
        const error = ext.runtime.lastError;
        if (error) reject(new Error(error.message));
        else resolve(response);
      });
    });
  }

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
    const scrollRange = (element) => element.scrollHeight - element.clientHeight;
    const isScrollable = (element) => {
      const style = getComputedStyle(element);
      return scrollRange(element) > 10 &&
        element.clientHeight > 0 &&
      (style.overflowY === "auto" || style.overflowY === "scroll" ||
        style.overflowY === "hidden");
    };
    const candidates = new Set(
      [...dialog.querySelectorAll("div")].filter(isScrollable)
    );
    if (isScrollable(dialog)) candidates.add(dialog);

    for (const link of dialog.querySelectorAll("a[href]")) {
      if (!usernameFromHref(link.getAttribute("href"))) continue;
      let ancestor = link.parentElement;
      while (ancestor && ancestor !== dialog) {
        if (candidates.has(ancestor)) return ancestor;
        ancestor = ancestor.parentElement;
      }
    }

    return [...candidates].sort((left, right) =>
      scrollRange(left) - scrollRange(right)
    )[0] || dialog;
  }

  function extractUsernames(dialog, users, avatars, targetCount = null) {
    let added = 0;
    for (const link of dialog.querySelectorAll("a[href]")) {
      if (targetCount !== null && users.size >= targetCount) break;
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

  async function scanDialog(listType, avatars, targetCount = null) {
    const dialog = findDialog();
    if (!dialog) throw new Error(`${listType} dialog not detected.`);
    if (targetCount === 0) return [];
    const users = new Set();
    let attempts = 0;
    let finalRecoveryAttempted = false;

    while (!state.stopRequested && attempts < 400) {
      const liveDialog = findDialog();
      if (!liveDialog) throw new Error(`${listType} dialog closed during scan.`);
      const container = findScrollContainer(liveDialog);
      const addedBeforeScroll = extractUsernames(
        liveDialog, users, avatars, targetCount
      );
      if (targetCount !== null && users.size >= targetCount) break;
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
      const addedAfterScroll = extractUsernames(
        refreshedDialog, users, avatars, targetCount
      );
      let added = addedBeforeScroll + addedAfterScroll;
      attempts += 1;
      sendCollectionState(true, `Scanning ${listType}: ${users.size} usernames found`, {
        listType, count: users.size, attempts
      });

      if (targetCount !== null && users.size >= targetCount) break;

      if (added === 0) {
        added = await waitForNewUsernames(listType, users, avatars) ? 1 : 0;
        if (targetCount !== null && users.size >= targetCount) break;
        if (added === 0 && targetCount !== null &&
            targetCount - users.size === 1 && !finalRecoveryAttempted) {
          finalRecoveryAttempted = true;
          const visibleLinks = [
            ...refreshedDialog.querySelectorAll("a[href]")
          ].filter((link) => link.offsetParent !== null);
          visibleLinks[visibleLinks.length - 1]?.scrollIntoView({ block: "end" });
          refreshedContainer.scrollTop = refreshedContainer.scrollHeight;
          refreshedContainer.dispatchEvent(new WheelEvent("wheel", {
            bubbles: true,
            cancelable: true,
            deltaY: Math.max(refreshedContainer.clientHeight, 300)
          }));
          if (await waitForNewUsernames(listType, users, avatars)) {
            if (users.size >= targetCount) break;
            continue;
          }
        }
        if (added === 0) {
          if (targetCount === null) break;
          const error = new Error(
            `${listType} list showed no new usernames after scrolling; ` +
            `${users.size} collected; Instagram shows ${targetCount}. ` +
            "The snapshot is partial."
          );
          error.collectedUsernames = [...users].sort();
          throw error;
        }
      }
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
    const match = text.match(/(\d[\d.,\s]*[kmb]?\+?)\s*([a-z()]+)/i);
    if (!match) return null;
    const label = match[2];
    const isExpectedLabel = listType === "followers"
      ? label.includes("follower") || label.includes("abonn") || label.includes("seguidor")
      : label.includes("following") || label.includes("suivi") ||
        label.includes("abonn") || label.includes("seguid");
    if (!isExpectedLabel) return null;

    const raw = match[1].replace(/[+\s]/g, "");
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

  function headerTotalIsExact(listType) {
    const label = headerTotalLabel(listType);
    if (!label) return false;
    const text = label.toLowerCase().normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "");
    const match = text.match(/(\d[\d.,\s]*[kmb]?\+?)\s*([a-z()]+)/i);
    return Boolean(match && !/[kmb+]/i.test(match[1]));
  }

  async function waitForNewUsernames(listType, users, avatars) {
    const deadline = Date.now() + NO_NEW_USER_WAIT_MS;
    while (Date.now() < deadline) {
      await sleep(250);
      const dialog = findDialog();
      if (!dialog) throw new Error(`${listType} dialog closed during scan.`);
      if (extractUsernames(dialog, users, avatars) > 0) return true;
    }
    return false;
  }

  async function openList(listType) {
    if (findDialog()) await closeDialog();
    const link = profileLink(listType);
    if (!link) throw new Error(`Could not find the ${listType} button on this profile.`);
    link.click();
    for (let attempt = 0; attempt < 30; attempt += 1) {
      await sleep(250);
      if (findDialog()) {
        await waitForDialogToStabilize(listType);
        return;
      }
    }
    throw new Error(`The ${listType} dialog did not open. Click the profile's ${listType} count once, then try again.`);
  }

  async function waitForDialogToStabilize(listType) {
    const startedAt = Date.now();
    let previousSignature = null;
    let stablePolls = 0;
    sendCollectionState(true, `Waiting for ${listType} list to finish loading...`);
    while (Date.now() - startedAt < 5000) {
      const dialog = findDialog();
      if (!dialog) throw new Error(`${listType} dialog closed while loading.`);
      const signature = [
        dialog.querySelectorAll("a[href]").length,
        dialog.scrollHeight,
        dialog.textContent.length
      ].join(":");
      if (signature === previousSignature) stablePolls += 1;
      else stablePolls = 0;
      if (Date.now() - startedAt >= 1000 && stablePolls >= 2) return;
      previousSignature = signature;
      await sleep(250);
    }
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
      complete: true,
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
        const headerTotalCount = result.headerTotals[listType];
        const exactHeaderTotal = headerTotalIsExact(listType)
          ? headerTotalCount
          : null;
        try {
          result[listType] = await scanDialog(
            listType, result.avatars, exactHeaderTotal
          );
        } catch (error) {
          if (Array.isArray(error.collectedUsernames)) {
            result[listType] = error.collectedUsernames;
          }
          throw error;
        }
        if (
          headerTotalCount !== null &&
          (exactHeaderTotal !== null
            ? result[listType].length !== exactHeaderTotal
            : result[listType].length < headerTotalCount)
        ) {
          const expected = exactHeaderTotal === null
            ? `at least ${headerTotalCount}`
            : `${headerTotalCount}`;
          throw new Error(
            `Only ${result[listType].length} ${listType} were collected, but the profile shows ${expected}. The snapshot is partial; try again after the list finishes loading.`
          );
        }
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
      const saveResponse = await sendRuntimeMessage({
        action: "saveCollection",
        collection: {
          profile: location.pathname.replace(/^\/|\/$/g, ""),
          followers: result.followers,
          following: result.following,
          avatars: result.avatars,
          headerTotals: result.headerTotals,
          headerTotalLabels: result.headerTotalLabels,
          complete: result.complete,
          error: result.error || (
            result.complete ? null : "Collection stopped before both lists finished."
          )
        }
      });
      if (!saveResponse?.ok) {
        throw new Error(saveResponse?.error || "Could not save the collection.");
      }
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
    sendRuntimeMessage({
      action: "collectionState",
      state: { active, status, ...details }
    }).catch(() => {});
  }

  ext.runtime.onMessage.addListener((message, _sender, sendResponse) => {
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
