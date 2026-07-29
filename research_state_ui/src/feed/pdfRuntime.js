export const PDF_ZOOM_STEPS = ['fit', 1.5, 2, 3];
export const PDF_FALLBACK_WIDTH = 570;

const MAX_RASTER_WIDTH = 4096;
const documentCache = new Map();
const pageCache = new Map();
let pdfjsPromise = null;

function loadPdfjs() {
  if (!pdfjsPromise) {
    pdfjsPromise = import('pdfjs-dist/build/pdf.mjs').then((pdfjs) => {
      if (!pdfjs.GlobalWorkerOptions.workerSrc) {
        pdfjs.GlobalWorkerOptions.workerSrc = new URL(
          'pdfjs-dist/build/pdf.worker.min.mjs',
          import.meta.url,
        ).href;
      }
      return pdfjs;
    });
  }
  return pdfjsPromise;
}

function loadPdfDocument(fetchUrl) {
  let cached = documentCache.get(fetchUrl);
  if (!cached) {
    cached = loadPdfjs()
      .then((pdfjs) => pdfjs.getDocument({ url: fetchUrl }).promise);
    documentCache.set(fetchUrl, cached);
    cached.catch(() => documentCache.delete(fetchUrl));
  }
  return cached;
}

export async function renderPdfPage(url, pageNumber, zoom, containerWidth) {
  const displayWidth = Math.round(containerWidth * (zoom === 'fit' ? 1 : zoom));
  const cacheKey = `${url}@${zoom}@${displayWidth}`;
  const cached = pageCache.get(cacheKey);
  if (cached) return cached;

  const document = await loadPdfDocument(url.split('#')[0]);
  const clampedPage = Math.min(Math.max(1, pageNumber), document.numPages);
  const page = await document.getPage(clampedPage);
  const naturalWidth = page.getViewport({ scale: 1 }).width;
  const rasterWidth = Math.min(
    displayWidth * (window.devicePixelRatio || 1),
    MAX_RASTER_WIDTH,
  );
  const viewport = page.getViewport({ scale: rasterWidth / naturalWidth });
  const canvas = document.createElement('canvas');
  canvas.width = viewport.width;
  canvas.height = viewport.height;
  await page.render({
    canvasContext: canvas.getContext('2d'),
    viewport,
  }).promise;
  const result = {
    dataUrl: canvas.toDataURL('image/png'),
    width: viewport.width,
    height: viewport.height,
    cssWidth: displayWidth,
    cssHeight: displayWidth * (viewport.height / viewport.width),
    numPages: document.numPages,
    clampedPage,
    zoom,
  };
  pageCache.set(cacheKey, result);
  return result;
}
