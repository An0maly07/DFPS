// Fallback demo video (PLAN.md §7): scripted walkthrough of the dashboard recorded with Playwright.
//   cd frontend && npx playwright install chromium   (once)
//   node scripts/record_demo.mjs [http://127.0.0.1:3000] [out_dir]
// Produces <out_dir>/horizon-demo.webm (~2 min): live view → Score now → 2021 flood replay
// with the model going Red before the peak → SAR-vs-Prithvi compare → 2018 drought replay.
import { chromium } from "playwright";
import { rename, mkdir } from "node:fs/promises";
import path from "node:path";

const url = process.argv[2] ?? "http://127.0.0.1:3000";
const outDir = process.argv[3] ?? "docs";
await mkdir(outDir, { recursive: true });

const browser = await chromium.launch();
const context = await browser.newContext({
  viewport: { width: 1440, height: 1000 },
  recordVideo: { dir: outDir, size: { width: 1440, height: 1000 } },
});
const page = await context.newPage();
const pause = (ms) => page.waitForTimeout(ms);
const setSlider = async (i) =>
  page.evaluate((v) => {
    const input = document.querySelector('input[type="range"]');
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(input, String(v));
    input.dispatchEvent(new Event("input", { bubbles: true }));
  }, i);

// 1. live dashboard
await page.goto(url, { waitUntil: "domcontentloaded", timeout: 180000 });
await page.getByText("Flood alert", { exact: false }).first().waitFor({ timeout: 120000 });
await pause(8000);
await page.getByRole("button", { name: /score now/i }).click().catch(() => {});
await pause(6000);
await page.mouse.wheel(0, 600);
await pause(4000);
await page.mouse.wheel(0, -600);

// 2. 2021 flood replay: scrub from 1 Jul to the peak
await page.locator("header select").nth(1).selectOption({ index: 1 });
await page.getByText("Replay day", { exact: false }).first().waitFor({ timeout: 120000 });
await pause(4000);
for (let i = 10; i <= 24; i++) {
  await setSlider(i);
  await pause(i >= 17 && i <= 22 ? 2500 : 900);
}
await pause(3000);

// 3. side-by-side SAR vs Prithvi at the peak
await setSlider(21);
await pause(2000);
await page.getByLabel("Compare SAR vs Prithvi").check();
await pause(14000);
await page.getByLabel("Compare SAR vs Prithvi").uncheck();
await pause(2000);

// 4. drought replay
await page.locator("header select").nth(1).selectOption({ index: 2 });
await page.getByText("Replay month", { exact: false }).first().waitFor({ timeout: 120000 });
await pause(4000);
for (let i = 0; i < 13; i++) {
  await setSlider(i);
  await pause(1500);
}
await page.mouse.wheel(0, 500);
await pause(5000);

const video = page.video();
await context.close();
const src = await video.path();
const dst = path.join(outDir, "horizon-demo.webm");
await rename(src, dst);
await browser.close();
console.log("wrote", dst);
