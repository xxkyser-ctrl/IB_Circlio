const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "content.js"), "utf8");

class FakeEvent {
  constructor(_type, options = {}) {
    Object.assign(this, options);
  }
}

class FakeLink {
  constructor(username, row) {
    this.href = `https://www.instagram.com/${username}/`;
    this.row = row;
    this.offsetParent = {};
    this.parentElement = { parentElement: row };
  }

  getAttribute(name) {
    if (name === "href") return `/${this.href.split("/").filter(Boolean).at(-1)}/`;
    return null;
  }

  querySelectorAll() {
    return [];
  }

  closest() {
    return this.row;
  }

  scrollIntoView() {}
}

class FakeCloseButton {
  constructor(closeDialog) {
    this.closeDialog = closeDialog;
  }

  getAttribute(name) {
    return name === "aria-label" ? "Close" : null;
  }

  getBoundingClientRect() {
    return { width: 20, height: 20, top: 110, right: 490 };
  }

  closest() {
    return this;
  }

  click() {
    this.closeDialog();
  }

  dispatchEvent() {}
}

class FakeList {
  constructor(total, pageSize, delayedPageChecks) {
    this.users = Array.from(
      { length: total },
      (_value, index) => `fixture_${String(index).padStart(4, "0")}`
    );
    this.pageSize = pageSize;
    this.loaded = Math.min(total, pageSize);
    this.visibleStart = 0;
    this.delayedPageChecks = delayedPageChecks;
    this.waitingChecks = 0;
  }

  get hasMore() {
    return this.loaded < this.users.length;
  }

  maybeLoadMore() {
    if (!this.hasMore) return;
    this.waitingChecks += 1;
    if (this.waitingChecks <= this.delayedPageChecks) return;
    this.loaded = Math.min(this.users.length, this.loaded + this.pageSize);
    this.waitingChecks = 0;
  }
}

function makeHarness(family, {
  followerCount,
  followingCount,
  followerHeader,
  followingHeader,
  delayedPageChecks = 0,
  stuckScroll = false
}) {
  const lists = {
    followers: new FakeList(followerCount, 40, delayedPageChecks),
    following: new FakeList(followingCount, 40, delayedPageChecks)
  };
  let currentList = null;
  let dialog = null;
  let savedCollection = null;
  let clock = 0;
  const listeners = [];
  const profileLinks = {
    followers: makeProfileLink("followers", followerCount, followerHeader),
    following: makeProfileLink("following", followingCount, followingHeader)
  };

  function makeProfileLink(listType, total, labelOverride) {
    return {
      href: `https://www.instagram.com/example/${listType}/`,
      offsetParent: {},
      textContent: labelOverride ||
        `${total >= 200 ? "200+" : total} ${listType}`,
      getAttribute(name) {
        return name === "href" ? `/example/${listType}/` : null;
      },
      click() {
        currentList = listType;
        const list = lists[listType];
        let scrollTop = 0;
        const container = {
          clientHeight: 100,
          get scrollHeight() {
            return Math.max(100, list.loaded * 40);
          },
          get scrollTop() {
            return scrollTop;
          },
          set scrollTop(value) {
            if (stuckScroll) return;
            scrollTop = Math.min(value, Math.max(0, this.scrollHeight - this.clientHeight));
            list.visibleStart = Math.floor(scrollTop / 40);
          },
          dispatchEvent() {
            if (this.scrollTop + this.clientHeight >= this.scrollHeight - 10) {
              list.maybeLoadMore();
            }
          }
        };
        const closeButton = new FakeCloseButton(() => {
          dialog = null;
          currentList = null;
        });
        dialog = {
          get scrollHeight() {
            return container.scrollHeight;
          },
          get textContent() {
            return list.users.slice(0, list.loaded).join(" ");
          },
          getBoundingClientRect() {
            return { top: 100, right: 500 };
          },
          dispatchEvent() {},
          querySelectorAll(selector) {
            if (selector === "div") return [container];
            if (selector === "a[href]") {
              return list.users.slice(list.visibleStart, list.visibleStart + 10).map((username) =>
                new FakeLink(username, { querySelectorAll: () => [] })
              );
            }
            if (selector.startsWith("button")) return [closeButton];
            return [];
          }
        };
      }
    };
  }

  const runtime = {
    lastError: null,
    onMessage: {
      addListener(listener) {
        listeners.push(listener);
      }
    },
    sendMessage(message, callback) {
      let response = { ok: true };
      if (message.action === "saveCollection") {
        savedCollection = message.collection;
        response = {
          ok: true,
          collection: {
            ...message.collection,
            id: 1
          }
        };
      }
      if (family === "firefox") return Promise.resolve(response);
      if (callback) callback(response);
      return undefined;
    }
  };
  const api = { runtime };
  const context = vm.createContext({
    chrome: family === "chrome" ? api : undefined,
    browser: family === "firefox" ? api : undefined,
    document: {
      querySelector(selector) {
        return selector === 'div[role="dialog"]' ? dialog : null;
      },
      querySelectorAll(selector) {
        return selector === "a[href]" ? Object.values(profileLinks) : [];
      },
      dispatchEvent() {}
    },
    location: {
      origin: "https://www.instagram.com",
      pathname: "/example/"
    },
    URL,
    HTMLElement: FakeCloseButton,
    WheelEvent: FakeEvent,
    MouseEvent: FakeEvent,
    KeyboardEvent: FakeEvent,
    getComputedStyle: () => ({ overflowY: "auto" }),
    Date: {
      now() {
        return clock;
      }
    },
    setTimeout(callback, milliseconds = 0) {
      clock += milliseconds;
      queueMicrotask(callback);
      return 1;
    },
    clearTimeout() {}
  });
  vm.runInContext(source, context, { filename: "content.js" });

  return {
    lists,
    get savedCollection() {
      return savedCollection;
    },
    get elapsed() {
      return clock;
    },
    async startCollection() {
      return new Promise((resolve) => {
        const listener = listeners[0];
        const returned = listener(
          { action: "startCollection" },
          {},
          resolve
        );
        assert.equal(returned, true);
      });
    }
  };
}

for (const family of ["chrome", "firefox"]) {
  test(`${family}: waits for delayed following pages before saving a complete snapshot`, async () => {
    const extension = makeHarness(family, {
      followerCount: 30,
      followingCount: 230,
      delayedPageChecks: 10
    });

    const response = await extension.startCollection();

    assert.equal(response.ok, true);
    assert.equal(response.collection.complete, true, response.error || "snapshot was incomplete");
    assert.equal(extension.savedCollection.followers.length, 30);
    assert.equal(extension.savedCollection.following.length, 230);
    assert.deepEqual(
      Array.from(extension.savedCollection.followers).sort(),
      extension.lists.followers.users
    );
    assert.deepEqual(
      Array.from(extension.savedCollection.following).sort(),
      extension.lists.following.users
    );
  });

  test(`${family}: reports and saves a header-count mismatch as partial`, async () => {
    const extension = makeHarness(family, {
      followerCount: 30,
      followingCount: 122,
      followingHeader: "200+ following"
    });

    const response = await extension.startCollection();

    assert.equal(response.ok, true);
    assert.equal(response.stopped, true);
    assert.match(
      response.error,
      /only 122 following were collected.*at least 200/i,
      response.error || "expected a header-count mismatch"
    );
    assert.equal(response.collection.complete, false);
    assert.equal(response.collection.error, response.error);
    assert.equal(extension.savedCollection.error, response.error);
    assert.equal(extension.savedCollection.following.length, 122);
  });

  test(`${family}: stops at an exact profile total instead of collecting trailing rows`, async () => {
    const extension = makeHarness(family, {
      followerCount: 435,
      followingCount: 3,
      followerHeader: "434 followers"
    });

    const response = await extension.startCollection();

    assert.equal(response.ok, true);
    assert.equal(response.collection.complete, true, response.error || "snapshot was incomplete");
    assert.equal(extension.savedCollection.followers.length, 434);
    assert.equal(extension.savedCollection.followers.includes("fixture_0434"), false);
  });

  test(`${family}: saves a partial snapshot promptly when scrolling stalls before the end`, async () => {
    const extension = makeHarness(family, {
      followerCount: 100,
      followingCount: 3,
      stuckScroll: true
    });

    const response = await extension.startCollection();

    assert.equal(response.ok, true);
    assert.equal(response.collection.complete, false);
    assert.match(response.error, /stopped making progress before the end/i);
    assert.ok(extension.elapsed < 40000, `scan took ${extension.elapsed}ms`);
  });
}
