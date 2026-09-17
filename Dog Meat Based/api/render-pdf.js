// api/render-pdf.js
//
// A Vercel Node.js serverless function that renders arbitrary HTML to a PDF
// using REAL headless Chromium — via @sparticuz/chromium + puppeteer-core.
// Both are just npm packages (no signup, no API key, no third-party
// service): @sparticuz/chromium ships a Chromium binary compressed small
// enough to fit inside a serverless function's size limit, which is exactly
// why this pattern is the standard way to do "Puppeteer on Vercel/Lambda".
//
// Because it's a real browser, page.pdf() here produces the same output as
// a user's own browser Print/Save — same fonts, same @media print rules,
// same watermark — not an approximation.
//
// ─── This lives in ONE Vercel project alongside your existing Python
//     functions. Vercel auto-detects the runtime per file extension, so a
//     .py file in /api uses the Python runtime and this .js file uses the
//     Node runtime — no vercel.json changes needed for that part.
//
// ─── package.json (add to this project, next to your Python requirements.txt):
//     {
//       "dependencies": {
//         "@sparticuz/chromium": "^123.0.0",
//         "puppeteer-core": "^22.0.0"
//       }
//     }
//     Check npm for the current @sparticuz/chromium version and use the
//     puppeteer-core version its README says it's tested against — the
//     bundled Chromium binary version and puppeteer-core version need to
//     be a matching pair.
//
// ─── Recommended vercel.json addition (this function needs more time/
//     memory than the Vercel free-tier default; requires a Pro plan or
//     higher for the extended duration):
//     {
//       "functions": {
//         "api/render-pdf.js": { "memory": 1024, "maxDuration": 30 }
//       }
//     }

const chromium = require("@sparticuz/chromium");
const puppeteer = require("puppeteer-core");

const MAX_HTML_BYTES = 5_000_000; // ~5MB sanity cap

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
      executablePath: await chromium.executablePath(),
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
