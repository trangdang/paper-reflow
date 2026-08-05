const fileInput = document.getElementById("file");
const urlInput = document.getElementById("url");
const fetchUrlBtn = document.getElementById("fetchUrl");
const statusEl = document.getElementById("status");
const downloadEl = document.getElementById("download");
const downloadInfoEl = document.getElementById("downloadInfo");

function setStatus(msg, { busy = false, error = false } = {}) {
  statusEl.className = error ? "error" : "";
  statusEl.innerHTML = busy ? `<span class="spinner"></span>${msg}` : msg;
}

let reflowBytes; // Python entry point, resolved once boot completes.

async function boot() {
  // indexURL defaults to the CDN's .../full/ dir, whose pyodide-lock.json
  // lists the bundled, ABI-matched PyMuPDF wheel.
  const pyodide = await loadPyodide();

  setStatus("Loading PDF engine (PyMuPDF)&hellip;", { busy: true });
  await pyodide.loadPackage("pymupdf");

  // Drop the reflow core onto sys.path (cwd is on it).
  const zipBuf = await (await fetch("./app.zip")).arrayBuffer();
  await pyodide.unpackArchive(zipBuf, "zip");

  reflowBytes = pyodide.runPython(`
from web_adapter import reflow_bytes
reflow_bytes
`);

  setStatus("Ready. Choose a PDF above.");
  fileInput.disabled = false;
  urlInput.disabled = false;
  fetchUrlBtn.disabled = false;
}

// arxiv.org/pdf/... already sends Access-Control-Allow-Origin: *, so a
// direct browser fetch works without a proxy; abs/ pages don't serve a
// PDF at all, so redirect those to the pdf/ path.
function normalizeUrl(raw) {
  const u = new URL(raw);
  if (/(^|\.)arxiv\.org$/.test(u.hostname)) {
    u.pathname = u.pathname.replace(/^\/abs\//, "/pdf/");
  }
  return u.toString();
}

async function reflowAndOffer(inBytes, downloadName) {
  downloadEl.hidden = true;
  downloadInfoEl.hidden = true;
  setStatus("Reflowing&hellip; (this can take a few seconds)", { busy: true });

  // JS Uint8Array -> Python bytes; result is a PyProxy over Python bytes.
  const outProxy = reflowBytes(inBytes);
  const outBytes = outProxy.toJs();
  outProxy.destroy();

  const url = URL.createObjectURL(
    new Blob([outBytes], { type: "application/pdf" }),
  );
  downloadEl.href = url;
  downloadEl.download = downloadName;
  downloadEl.hidden = false;

  const timestamp = new Date().toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
  downloadInfoEl.textContent = `${downloadName} — generated ${timestamp}`;
  downloadInfoEl.hidden = false;

  setStatus("Done.");
}

fileInput.addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  fileInput.disabled = true;
  try {
    const inBytes = new Uint8Array(await file.arrayBuffer());
    await reflowAndOffer(
      inBytes,
      `${file.name.replace(/\.pdf$/i, "")}.reflowed.pdf`,
    );
  } catch (err) {
    console.error(err);
    setStatus(`Failed to reflow this PDF: ${err.message || err}`, {
      error: true,
    });
  } finally {
    fileInput.disabled = false;
  }
});

fetchUrlBtn.addEventListener("click", async () => {
  const raw = urlInput.value.trim();
  if (!raw) return;

  urlInput.disabled = true;
  fetchUrlBtn.disabled = true;
  try {
    const url = normalizeUrl(raw);
    setStatus("Downloading PDF&hellip;", { busy: true });

    let resp;
    try {
      resp = await fetch(url);
    } catch (_networkErr) {
      throw new Error(
        "Could not fetch that URL from your browser (likely blocked by CORS). " +
          "arXiv links work directly; for other sites, download the PDF and use " +
          "the file picker above instead.",
      );
    }
    if (!resp.ok)
      throw new Error(`Server returned ${resp.status} ${resp.statusText}`);
    const contentType = resp.headers.get("content-type") || "";
    const inBytes = new Uint8Array(await resp.arrayBuffer());
    if (
      !contentType.includes("pdf") &&
      inBytes.slice(0, 4).join(",") !== "37,80,68,70"
    ) {
      throw new Error("That URL didn't return a PDF.");
    }

    const name =
      decodeURIComponent(url.split("/").pop().split("?")[0]) || "document";
    await reflowAndOffer(
      inBytes,
      `${name.replace(/\.pdf$/i, "")}.reflowed.pdf`,
    );
  } catch (err) {
    console.error(err);
    setStatus(err.message || String(err), { error: true });
  } finally {
    urlInput.disabled = false;
    fetchUrlBtn.disabled = false;
  }
});

boot().catch((err) => {
  console.error(err);
  setStatus(`Failed to load: ${err.message || err}`, { error: true });
});
