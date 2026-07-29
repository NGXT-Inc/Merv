export function pdfPageInfo(post, preview) {
  const url = post?.link_url || '';
  if (!url) return null;
  let parsed;
  try {
    parsed = new URL(url);
  } catch {
    return null;
  }
  const looksLikePdf = /\.pdf$/i.test(parsed.pathname) || /\/pdf\//i.test(parsed.pathname);
  const pageMatch = /(?:^|&)page=(\d+)/i.exec(parsed.hash.replace(/^#/, ''));
  const page = pageMatch ? parseInt(pageMatch[1], 10) : null;
  if (!looksLikePdf && page == null) return null;
  const kind = preview?.kind;
  if (kind && kind !== 'paper' && kind !== 'page') return null;
  const arxivId = arxivIdFromUrl(url);
  return {
    url,
    page: page || 1,
    title: preview?.title || titleFromUrl(arxivId),
    authors: preview?.authors || [],
    year: preview?.year || '',
    arxivId,
    host: hostFromUrl(url),
  };
}

function arxivIdFromUrl(url) {
  const match = /arxiv\.org\/(?:pdf|abs)\/([\w.]+)/i.exec(url);
  return match ? match[1].replace(/v\d+$/, '') : null;
}

function titleFromUrl(arxivId) {
  return arxivId ? `arXiv:${arxivId}` : 'PDF document';
}

function hostFromUrl(url) {
  try {
    return new URL(url).hostname.replace(/^www\./, '');
  } catch {
    return url;
  }
}

function firstAuthorSurname(authors) {
  if (!authors?.length) return '';
  const author = authors[0];
  return (
    author.includes(',') ? author.split(',')[0] : author.split(' ').pop()
  ).trim();
}

export function pdfByline(info) {
  const parts = [];
  const surname = firstAuthorSurname(info.authors);
  if (surname) {
    parts.push(info.authors.length > 1 ? `${surname} et al.` : surname);
  }
  if (info.year) parts.push(String(info.year));
  parts.push(`page ${info.page}`);
  return parts.join(' · ');
}

export function pdfFallbackLabel(info) {
  const lead = info.arxivId ? `arXiv:${info.arxivId}` : info.host;
  return `${lead} · page ${info.page}`;
}
