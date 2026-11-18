import { test as setup } from "@playwright/test";

import { authFile, IDENTITIES, loginViaUi } from "./helpers";

// Log in once per seeded identity through the real login form and save the session cookie for the other specs.
for (const identity of IDENTITIES) {
  setup(`sign in as ${identity.role} of organisation ${identity.org.toUpperCase()}`, async ({ page }) => {
    await loginViaUi(page, identity.role, identity.org);
    await page.context().storageState({ path: authFile(identity.key) });
  });
}
