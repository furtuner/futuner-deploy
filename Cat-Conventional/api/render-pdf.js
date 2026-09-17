// api/render-pdf.js
//
// A Vercel Node.js serverless function that renders arbitrary HTML to a PDF
// using REAL headless Chromium — via @sparticuz/chromium-min + puppeteer-core.
//
// NOTE: this uses the "-min" variant of @sparticuz/chromium, not the
// regular package. The regular package assumes the host OS already
// provides certain shared libraries (libnss3.so, etc.) — true on AWS
// Lambda, but not reliably true on Vercel's Node.js runtime, which is
// what caused "libnss3.so: cannot open shared object file" errors here.
// The "-min" variant downloads a complete pack (Chromium + every shared
// library it needs) from GitHub at cold start instead of assuming the
// host provides them, which fixes that class of error at the cost of a
// slightly slower cold start on the first request after a deploy.
//
// ─── package.json (add to this project, next to your Python requirements.txt):
//     {
//       "dependencies": {
//         "@sparticuz/chromium-min": "^131.0.1",
//         "puppeteer-core": "^23.11.1"
//       }
//     }
//     If you bump the version, update CHROMIUM_PACK_URL below to match —
//     the pack URL's version must match the installed package version.
//
// ─── Recommended vercel.json addition (already present in this project's
//     vercel.json, inside the render-pdf.js build's "config" object):
//     { "memory": 1024, "maxDuration": 30 }
//     Requires a Vercel Pro plan or higher for maxDuration above 10s.

const chromium = require("@sparticuz/chromium-min");
const puppeteer = require("puppeteer-core");

const MAX_HTML_BYTES = 5_000_000; // ~5MB sanity cap

// Must match the @sparticuz/chromium-min version in package.json.
const CHROMIUM_PACK_URL =
  "https://github.com/Sparticuz/chromium/releases/download/v131.0.1/chromium-v131.0.1-pack.tar";

module.exports = async (req, res) => {
  if (req.method !== "POST") {
    res.status(405).json({ error: "Use POST" });
    return;
  }

  const { html } = req.body || {};
  if (typeof html !== "string" || html.length === 0) {
    res.status(400).json({ error: "Missing 'html' string in request body." });
    return;
  }
  if (Buffer.byteLength(html, "utf8") > MAX_HTML_BYTES) {
    res.status(413).json({ error: "HTML payload too large." });
    return;
  }

  let browser;
  try {
    browser = await puppeteer.launch({
      args: chromium.args,
      executablePath: await chromium.executablePath(CHROMIUM_PACK_URL),
      headless: chromium.headless,
    });

    const page = await browser.newPage();
    await page.setContent(html, { waitUntil: "networkidle0" });
    // Same media type a browser applies when you hit Print/Save — makes
    // @media print rules (like the @page landscape/margin rule) take effect.
    await page.emulateMediaType("print");

    const pdfBuffer = await page.pdf({
      printBackground: true,
      landscape: true,
    });

    res.setHeader("Content-Type", "application/pdf");
    res.status(200).send(pdfBuffer);
  } catch (err) {
    res.status(500).json({ error: `PDF rendering failed: ${err.message}` });
  } finally {
    if (browser) await browser.close();
  }
};
