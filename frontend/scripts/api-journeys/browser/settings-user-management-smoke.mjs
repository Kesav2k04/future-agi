import { createRequire } from "node:module";
import process from "node:process";
import {
  apiPath,
  asArray,
  assert,
  createAuthenticatedContext,
  currentUserEmail,
  currentUserId,
  envFlag,
} from "../lib/api-client.mjs";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const SCREENSHOT_PATH = "/tmp/settings-user-management-smoke.png";
const INVITE_SCREENSHOT_PATH = "/tmp/settings-user-management-invite-smoke.png";
const CANCEL_SCREENSHOT_PATH = "/tmp/settings-user-management-cancel-smoke.png";
const ERROR_SCREENSHOT_PATH = "/tmp/settings-user-management-error-smoke.png";
const ORG_ADMIN_LEVEL = 8;

async function main() {
  const auth = await createAuthenticatedContext();
  const email = currentUserEmail(auth.user);
  const userId = currentUserId(auth.user);
  assert(email, "Authenticated user did not include an email.");
  assert(userId, "Authenticated user did not include an id.");

  const memberPayload = await auth.client.get(
    apiPath("/accounts/organization/members/"),
    { query: { search: email, page: 1, limit: 10 } },
  );
  const memberRows = asArray(memberPayload);
  const memberRow = memberRows.find(
    (row) =>
      row?.id === userId || row?.email?.toLowerCase() === email.toLowerCase(),
  );
  assert(
    memberRow,
    "RBAC member list did not return the current user by email.",
  );
  const shouldMutate = envFlag("API_JOURNEY_MUTATIONS");
  const inviteEmail =
    `ui.journey.rbac.${auth.runId.replace(/[^a-z0-9-]/gi, "").slice(0, 20)}@futureagi.local`.toLowerCase();
  let inviteCancelled = !shouldMutate;

  const apiFailures = [];
  const pageErrors = [];
  let stage = "starting";
  const evidence = {
    email,
    user_id: userId,
    member_status: memberRow.status,
    member_org_level: memberRow.org_level,
    member_type: memberRow.type,
    invite_mutation_exercised: shouldMutate,
  };

  const browser = await puppeteer.launch({
    executablePath: browserExecutablePath(),
    headless: process.env.HEADLESS !== "0",
    defaultViewport: { width: 1440, height: 950 },
    args: ["--no-sandbox"],
  });

  const page = await browser.newPage();
  await page.setBypassServiceWorker(true);
  await installRuntimeConfig(page, auth);
  await page.evaluateOnNewDocument(
    ({ tokens, organizationId, workspaceId, user }) => {
      localStorage.setItem("accessToken", tokens.access);
      localStorage.setItem("refreshToken", tokens.refresh || "");
      localStorage.setItem("rememberMe", "true");
      localStorage.setItem("initial-render", "done");
      if (organizationId)
        sessionStorage.setItem("organizationId", organizationId);
      if (workspaceId) sessionStorage.setItem("workspaceId", workspaceId);
      if (user?.id)
        sessionStorage.setItem("futureagi-current-user-id", user.id);
    },
    {
      tokens: auth.tokens,
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
      user: auth.user,
    },
  );
  page.on("response", (response) => {
    const url = response.url();
    if (
      (url.includes("/accounts/organization/members/") ||
        url.includes("/accounts/organization/invite/") ||
        url.includes("/accounts/workspace/list/") ||
        url.includes("/accounts/user-info/")) &&
      response.status() >= 400
    ) {
      apiFailures.push(`${response.status()} ${url}`);
    }
  });
  page.on("pageerror", (error) => pageErrors.push(error.message));

  try {
    stage = "load user management page";
    const initialMembersResponse = page.waitForResponse(
      (response) =>
        response.url().includes("/accounts/organization/members/") &&
        response.status() < 400,
      { timeout: 60000 },
    );
    await page.goto(`${APP_BASE}/dashboard/settings/user-management`, {
      waitUntil: "domcontentloaded",
    });
    await initialMembersResponse;

    await waitForVisibleText(page, "Members", { exact: true });
    await page.waitForSelector('input[placeholder="Search by name or email"]', {
      visible: true,
      timeout: 30000,
    });
    await waitForVisibleText(page, "Invite User", { exact: true });

    await waitForVisibleText(page, email);
    await waitForVisibleText(page, "Organisation Role", { exact: true });
    await waitForVisibleText(page, "Workspaces", { exact: true });
    await waitForVisibleText(page, "Status", { exact: true });

    const searchedMembersResponse = page.waitForResponse(
      (response) =>
        response.url().includes("/accounts/organization/members/") &&
        response.url().includes(`search=${encodeURIComponent(email)}`) &&
        response.status() < 400,
      { timeout: 60000 },
    );
    await page.click('input[placeholder="Search by name or email"]', {
      clickCount: 3,
    });
    await page.type('input[placeholder="Search by name or email"]', email);
    await searchedMembersResponse;
    await waitForVisibleText(page, email);

    await clickVisibleText(page, "Invite User", { exact: true });
    await waitForVisibleText(page, "Invite new users", { exact: true });
    await waitForVisibleText(page, "Emails", { exact: true });

    if (shouldMutate) {
      stage = "assert current user can invite";
      assert(
        Number(memberRow.org_level || 0) >= ORG_ADMIN_LEVEL,
        "Current user is not an org admin; browser invite mutation is unsafe.",
      );

      stage = "submit browser invite";
      const inviteResponsePromise = page.waitForResponse(
        (response) =>
          response.url().includes("/accounts/organization/invite/") &&
          response.request().method() === "POST",
        { timeout: 60000 },
      );
      await fillChipsInput(page, "Emails, comma separated", inviteEmail);
      await selectSearchFieldOption(page, "Organization Role", "Viewer");
      await clickEnabledButton(page, "Send Invite");
      const inviteResponse = await inviteResponsePromise;
      const inviteBody = await inviteResponse.json().catch(() => null);
      assert(
        inviteResponse.status() < 400,
        `Browser invite create failed with HTTP ${inviteResponse.status()}: ${JSON.stringify(inviteBody)}`,
      );
      const inviteResult = inviteBody?.result || inviteBody;
      assert(
        asArray(inviteResult?.invited).includes(inviteEmail),
        "Browser invite create response did not include the disposable email.",
      );
      await waitForNoVisibleText(page, "Invite new users", { exact: true });

      stage = "verify browser invite row";
      await searchMembers(page, inviteEmail);
      await waitForVisibleText(page, inviteEmail);
      await waitForVisibleText(page, "Pending", { exact: true });
      let inviteRows = asArray(
        await auth.client.get(apiPath("/accounts/organization/members/"), {
          query: { search: inviteEmail, page: 1, limit: 10 },
        }),
      );
      const pendingInvite = inviteRows.find(
        (row) =>
          row?.type === "invite" &&
          String(row?.email || "").toLowerCase() === inviteEmail,
      );
      assert(
        pendingInvite?.status === "Pending",
        "Member list API did not expose the browser-created pending invite.",
      );
      evidence.invite_email = inviteEmail;
      evidence.invite_id = pendingInvite.id;
      evidence.invite_screenshot = INVITE_SCREENSHOT_PATH;
      await page.screenshot({ path: INVITE_SCREENSHOT_PATH, fullPage: true });

      stage = "open pending invite action menu";
      await clickMemberRowActionMenu(page, inviteEmail);
      await waitForVisibleText(page, "Cancel invite", { exact: true });
      stage = "open cancel invite confirmation";
      await clickVisibleTextElement(page, "Cancel invite", { exact: true });
      await waitForVisibleText(
        page,
        "Are you sure you want to cancel this invite?",
        {
          exact: true,
        },
      );
      stage = "submit browser invite cancel";
      const cancelResponsePromise = page.waitForResponse(
        (response) =>
          response.url().includes("/accounts/organization/invite/cancel/") &&
          response.request().method() === "DELETE",
        { timeout: 60000 },
      );
      await clickDialogButton(page, "Remove");
      const cancelResponse = await cancelResponsePromise;
      const cancelBody = await cancelResponse.json().catch(() => null);
      assert(
        cancelResponse.status() < 400,
        `Browser invite cancel failed with HTTP ${cancelResponse.status()}: ${JSON.stringify(cancelBody)}`,
      );
      assert(
        String(cancelBody?.result?.message || cancelBody?.message || "")
          .toLowerCase()
          .includes("cancel"),
        "Browser invite cancel response did not report cancellation.",
      );

      stage = "verify browser invite cancel";
      await waitForNoVisibleText(page, inviteEmail);
      inviteRows = asArray(
        await auth.client.get(apiPath("/accounts/organization/members/"), {
          query: { search: inviteEmail, page: 1, limit: 10 },
        }),
      );
      assert(
        !inviteRows.some(
          (row) => String(row?.email || "").toLowerCase() === inviteEmail,
        ),
        "Cancelled invite was still visible in the organization members API.",
      );
      inviteCancelled = true;
      evidence.cancel_screenshot = CANCEL_SCREENSHOT_PATH;
      await page.screenshot({ path: CANCEL_SCREENSHOT_PATH, fullPage: true });
    } else {
      stage = "close read-only invite drawer";
      await page.keyboard.press("Escape");
      await waitForNoVisibleText(page, "Invite new users", { exact: true });
    }

    stage = "final assertions";
    await waitForNoVisibleText(page, "Invalid Date");
    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });
    evidence.screenshot = SCREENSHOT_PATH;

    assert(apiFailures.length === 0, `API failures: ${apiFailures.join("; ")}`);
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);

    console.log(
      JSON.stringify(
        {
          status: "passed",
          app_base: APP_BASE,
          api_base: auth.apiBase,
          organization_id: auth.organizationId,
          workspace_id: auth.workspaceId,
          evidence,
        },
        null,
        2,
      ),
    );
  } catch (error) {
    await page.screenshot({ path: ERROR_SCREENSHOT_PATH, fullPage: true });
    console.error(
      JSON.stringify(
        {
          status: "failed",
          error: error.message,
          stage,
          api_failures: apiFailures,
          debug: await collectDebugState(page),
          error_screenshot: ERROR_SCREENSHOT_PATH,
        },
        null,
        2,
      ),
    );
    throw error;
  } finally {
    if (shouldMutate && !inviteCancelled) {
      await cancelInvitesByEmail(auth, inviteEmail);
    }
    await browser.close();
  }
}

async function installRuntimeConfig(page, auth) {
  await page.setRequestInterception(true);
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname === "/config.js") {
      request.respond({
        status: 200,
        contentType: "application/javascript",
        body: `window.__FUTURE_AGI_CONFIG__ = ${JSON.stringify({
          VITE_HOST_API: auth.apiBase,
          VITE_ASSETS_API: APP_BASE,
        })};`,
      });
      return;
    }
    request.continue();
  });
}

async function fillChipsInput(page, placeholder, value) {
  const selector = `input[placeholder="${cssString(placeholder)}"]`;
  await page.waitForSelector(selector, { visible: true, timeout: 30000 });
  await page.click(selector);
  await page.keyboard.type(value);
  await page.keyboard.press("Enter");
  await waitForVisibleText(page, value, { exact: true });
}

async function selectSearchFieldOption(page, label, option) {
  const clicked = await page.evaluate((expectedLabel) => {
    const normalize = (value) =>
      String(value || "")
        .replace(/\s*\*$/, "")
        .trim();
    const visible = (element) => {
      const style = window.getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return (
        style.visibility !== "hidden" &&
        style.display !== "none" &&
        rect.width > 0 &&
        rect.height > 0
      );
    };
    const labelElement = Array.from(document.querySelectorAll("label")).find(
      (candidate) =>
        visible(candidate) &&
        normalize(candidate.textContent) === expectedLabel,
    );
    const root =
      labelElement?.closest(".MuiFormControl-root") ||
      labelElement?.parentElement?.parentElement;
    const input = root?.querySelector("input");
    if (!input) return false;
    input.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
    input.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
    input.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    input.focus();
    return true;
  }, label);
  assert(clicked, `Could not open search select ${label}.`);
  await clickVisibleTextElement(page, option, {
    exact: true,
    selector: '[role="menuitem"], li',
  });
}

async function searchMembers(page, search) {
  const searchedMembersResponse = page.waitForResponse(
    (response) =>
      response.url().includes("/accounts/organization/members/") &&
      new URL(response.url()).searchParams.get("search") === search &&
      response.status() < 400,
    { timeout: 60000 },
  );
  const selector = 'input[placeholder="Search by name or email"]';
  await page.waitForSelector(selector, { visible: true, timeout: 30000 });
  await page.click(selector, { clickCount: 3 });
  await page.keyboard.press("Backspace");
  await page.type(selector, search);
  await searchedMembersResponse;
}

async function clickEnabledButton(page, text) {
  await clickableTextBox(page, text, {
    exact: true,
    selector: "button",
    enabledOnly: true,
  });
  const clicked = await page.evaluate((expectedText) => {
    const button = Array.from(document.querySelectorAll("button")).find(
      (candidate) =>
        !candidate.disabled &&
        String(candidate.textContent || "").trim() === expectedText,
    );
    if (!button) return false;
    button.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
    button.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
    button.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    button.click();
    return true;
  }, text);
  assert(clicked, `Could not click enabled button ${text}.`);
}

async function clickDialogButton(page, text) {
  await page.waitForFunction(
    (expectedText) => {
      const dialog = document.querySelector('[role="dialog"]');
      if (!dialog) return false;
      return Array.from(dialog.querySelectorAll("button")).some((button) => {
        const style = window.getComputedStyle(button);
        const rect = button.getBoundingClientRect();
        return (
          style.visibility !== "hidden" &&
          style.display !== "none" &&
          rect.width > 0 &&
          rect.height > 0 &&
          !button.disabled &&
          String(button.textContent || "").trim() === expectedText
        );
      });
    },
    { timeout: 30000 },
    text,
  );
  const box = await page.evaluate((expectedText) => {
    const dialog = document.querySelector('[role="dialog"]');
    const button = Array.from(dialog.querySelectorAll("button")).find(
      (candidate) => {
        const style = window.getComputedStyle(candidate);
        const rect = candidate.getBoundingClientRect();
        return (
          style.visibility !== "hidden" &&
          style.display !== "none" &&
          rect.width > 0 &&
          rect.height > 0 &&
          !candidate.disabled &&
          String(candidate.textContent || "").trim() === expectedText
        );
      },
    );
    if (!button) return null;
    button.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
    button.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
    button.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    button.click();
    const rect = button.getBoundingClientRect();
    return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
  }, text);
  assert(box, `Could not click dialog button ${text}.`);
}

async function clickMemberRowActionMenu(page, email) {
  await page.waitForFunction(
    (expectedEmail) =>
      Array.from(document.querySelectorAll('[role="row"]')).some((row) =>
        String(row.textContent || "")
          .toLowerCase()
          .includes(expectedEmail),
      ),
    { timeout: 30000 },
    email,
  );
  const strategies = [
    "action-cell-center",
    "action-icon-center",
    "row-right-center",
    "dispatch-stack-click",
  ];
  for (const strategy of strategies) {
    const box = await page.evaluate(
      ({ expectedEmail, strategy: clickStrategy }) => {
        const row = Array.from(document.querySelectorAll('[role="row"]')).find(
          (candidate) =>
            String(candidate.textContent || "")
              .toLowerCase()
              .includes(expectedEmail),
        );
        const actionCell = row?.querySelector('[col-id="action"]');
        if (!row || !actionCell) return null;
        if (clickStrategy === "dispatch-stack-click") {
          const target =
            actionCell.querySelector(".MuiStack-root") ||
            actionCell.querySelector("svg") ||
            actionCell;
          target.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
          target.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
          target.dispatchEvent(new MouseEvent("click", { bubbles: true }));
          target.click?.();
          return { dispatched: true };
        }
        let target = actionCell;
        if (clickStrategy === "action-icon-center") {
          target =
            actionCell.querySelector(
              "svg, [class*='Iconify'], [data-testid]",
            ) || actionCell;
        }
        const rect =
          clickStrategy === "row-right-center"
            ? row.getBoundingClientRect()
            : target.getBoundingClientRect();
        if (clickStrategy === "row-right-center") {
          return { x: rect.right - 24, y: rect.top + rect.height / 2 };
        }
        return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
      },
      { expectedEmail: email, strategy },
    );
    assert(box, `Could not find action cell for ${email}.`);
    if (!box.dispatched) {
      await page.mouse.click(box.x, box.y);
    }
    const opened = await page
      .waitForFunction(
        () =>
          document.body.innerText.includes("Cancel invite") ||
          document.body.innerText.includes("Resend the invite"),
        { timeout: 1000 },
      )
      .then(() => true)
      .catch(() => false);
    if (opened) return;
  }
  throw new Error(`Could not open action menu for ${email}.`);
}

async function clickVisibleTextElement(
  page,
  text,
  { exact = false, selector = "body *" } = {},
) {
  const box = await clickableTextBox(page, text, { exact, selector });
  await page.mouse.click(box.x, box.y);
}

async function clickableTextBox(
  page,
  text,
  { exact = false, selector = "body *", enabledOnly = false } = {},
) {
  await page.waitForFunction(
    ({
      text: expectedText,
      exact: exactMatch,
      selector: targetSelector,
      enabledOnly: enabled,
    }) => {
      const normalize = (value) => String(value || "").trim();
      const visible = (element) => {
        const style = window.getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return (
          style.visibility !== "hidden" &&
          style.display !== "none" &&
          rect.width > 0 &&
          rect.height > 0
        );
      };
      return Array.from(document.querySelectorAll(targetSelector)).some(
        (candidate) => {
          if (!visible(candidate)) return false;
          if (enabled && candidate.disabled) return false;
          const textContent = normalize(candidate.textContent);
          return exactMatch
            ? textContent === expectedText
            : textContent.includes(expectedText);
        },
      );
    },
    { timeout: 30000 },
    { text, exact, selector, enabledOnly },
  );
  const box = await page.evaluate(
    ({
      text: expectedText,
      exact: exactMatch,
      selector: targetSelector,
      enabledOnly: enabled,
    }) => {
      const normalize = (value) => String(value || "").trim();
      const visible = (element) => {
        const style = window.getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return (
          style.visibility !== "hidden" &&
          style.display !== "none" &&
          rect.width > 0 &&
          rect.height > 0
        );
      };
      const elements = Array.from(document.querySelectorAll(targetSelector))
        .filter((candidate) => {
          if (!visible(candidate)) return false;
          if (enabled && candidate.disabled) return false;
          const textContent = normalize(candidate.textContent);
          return exactMatch
            ? textContent === expectedText
            : textContent.includes(expectedText);
        })
        .sort((a, b) => {
          const aRect = a.getBoundingClientRect();
          const bRect = b.getBoundingClientRect();
          return aRect.width * aRect.height - bRect.width * bRect.height;
        });
      const element = elements[0];
      if (!element) return null;
      const rect = element.getBoundingClientRect();
      return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
    },
    { text, exact, selector, enabledOnly },
  );
  assert(box, `Could not find visible text ${text}.`);
  return box;
}

async function clickVisibleText(
  page,
  text,
  { exact = false, timeout = 30000 } = {},
) {
  await waitForVisibleText(page, text, { exact, timeout });
  await page.evaluate(
    ({ text: expectedText, exact: exactMatch }) => {
      const normalized = (value) => String(value || "").trim();
      const isVisible = (element) => {
        const style = window.getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return (
          style.visibility !== "hidden" &&
          style.display !== "none" &&
          rect.width > 0 &&
          rect.height > 0
        );
      };
      const element = Array.from(
        document.querySelectorAll("button, [role='button']"),
      ).find((candidate) => {
        if (!isVisible(candidate)) return false;
        const textContent = normalized(candidate.textContent);
        if (exactMatch) return textContent === expectedText;
        return textContent.includes(expectedText);
      });
      if (!element)
        throw new Error(`No visible clickable text: ${expectedText}`);
      element.click();
    },
    { text, exact },
  );
}

async function waitForVisibleText(
  page,
  text,
  { exact = false, timeout = 30000 } = {},
) {
  await page.waitForFunction(
    ({ text: expectedText, exact: exactMatch }) => {
      const normalized = (value) => String(value || "").trim();
      const isVisible = (element) => {
        const style = window.getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return (
          style.visibility !== "hidden" &&
          style.display !== "none" &&
          rect.width > 0 &&
          rect.height > 0
        );
      };
      return Array.from(document.querySelectorAll("body *")).some((element) => {
        if (!isVisible(element)) return false;
        const textContent = normalized(element.textContent);
        if (exactMatch) return textContent === expectedText;
        return textContent.includes(expectedText);
      });
    },
    { timeout },
    { text, exact },
  );
}

async function waitForNoVisibleText(
  page,
  text,
  { exact = false, timeout = 30000 } = {},
) {
  await page.waitForFunction(
    ({ text: expectedText, exact: exactMatch }) => {
      const normalized = (value) => String(value || "").trim();
      const isVisible = (element) => {
        const style = window.getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return (
          style.visibility !== "hidden" &&
          style.display !== "none" &&
          rect.width > 0 &&
          rect.height > 0
        );
      };
      return !Array.from(document.querySelectorAll("body *")).some(
        (element) => {
          if (!isVisible(element)) return false;
          const textContent = normalized(element.textContent);
          if (exactMatch) return textContent === expectedText;
          return textContent.includes(expectedText);
        },
      );
    },
    { timeout },
    { text, exact },
  );
}

async function cancelInvitesByEmail(auth, email) {
  const rows = asArray(
    await auth.client.get(apiPath("/accounts/organization/members/"), {
      query: { search: email, page: 1, limit: 10 },
    }),
  );
  for (const row of rows) {
    if (
      row?.type === "invite" &&
      String(row?.email || "").toLowerCase() === email
    ) {
      await auth.client.delete(
        apiPath("/accounts/organization/invite/cancel/"),
        {
          body: { invite_id: row.id },
        },
      );
    }
  }
}

async function collectDebugState(page) {
  return page.evaluate(() => {
    const visible = (element) => {
      const style = window.getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return (
        style.visibility !== "hidden" &&
        style.display !== "none" &&
        rect.width > 0 &&
        rect.height > 0
      );
    };
    return {
      path: window.location.pathname,
      visibleText: String(document.body?.innerText || "").slice(0, 3000),
      inputs: Array.from(document.querySelectorAll("input"))
        .filter(visible)
        .map((input) => ({
          placeholder: input.getAttribute("placeholder") || "",
          value: input.value,
          disabled: input.disabled,
        })),
      buttons: Array.from(document.querySelectorAll("button"))
        .filter(visible)
        .map((button) => ({
          text: String(button.textContent || "").trim(),
          disabled: button.disabled,
        })),
      rows: Array.from(document.querySelectorAll('[role="row"]'))
        .filter(visible)
        .slice(0, 10)
        .map((row) => String(row.textContent || "").trim()),
    };
  });
}

function cssString(value) {
  return String(value).replace(/\\/g, "\\\\").replace(/"/g, '\\"');
}

function browserExecutablePath() {
  if (process.env.PUPPETEER_EXECUTABLE_PATH) {
    return process.env.PUPPETEER_EXECUTABLE_PATH;
  }
  if (process.env.CHROME_PATH) return process.env.CHROME_PATH;
  if (process.platform === "darwin") {
    return "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
  }
  if (process.platform === "linux") {
    return "/usr/bin/google-chrome";
  }
  return undefined;
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
