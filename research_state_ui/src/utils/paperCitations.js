const ARXIV_ID_SOURCE = String.raw`(?:\d{4}\.\d{4,5}|[a-z][a-z0-9.-]*\/\d{7})`;
const DOI_SOURCE = String.raw`10\.\d{4,9}\/[-._;()/:a-z0-9]+`;

const SCHOLARLY_HOSTS = [
  'aclanthology.org',
  'dl.acm.org',
  'ieeexplore.ieee.org',
  'jmlr.org',
  'link.springer.com',
  'nature.com',
  'ncbi.nlm.nih.gov',
  'openaccess.thecvf.com',
  'openreview.net',
  'papers.nips.cc',
  'proceedings.mlr.press',
  'pubmed.ncbi.nlm.nih.gov',
  'science.org',
];

function cleanTrailingPunctuation(raw) {
  let value = String(raw || '').trim().replace(/[.,;:]+$/g, '');
  for (const [open, close] of [['(', ')'], ['[', ']'], ['{', '}']]) {
    while (value.endsWith(close)) {
      const opens = value.split(open).length - 1;
      const closes = value.split(close).length - 1;
      if (closes <= opens) break;
      value = value.slice(0, -1);
    }
  }
  return value;
}

function usableTitle(raw) {
  const title = String(raw || '').replace(/\s+/g, ' ').trim();
  if (!title || /^https?:/i.test(title) || /^(?:arxiv|doi)\s*:/i.test(title)) return null;
  return title;
}

function normalizeArxivId(raw) {
  const value = cleanTrailingPunctuation(raw).replace(/\.pdf$/i, '');
  const match = value.match(new RegExp(`^(${ARXIV_ID_SOURCE})(?:v\\d+)?$`, 'i'));
  return match ? match[1].toLowerCase() : null;
}

function normalizeDoi(raw) {
  const value = cleanTrailingPunctuation(raw)
    .replace(/^doi\s*:\s*/i, '')
    .replace(/^https?:\/\/(?:dx\.)?doi\.org\//i, '');
  const match = value.match(new RegExp(`^(${DOI_SOURCE})$`, 'i'));
  return match ? match[1].toLowerCase() : null;
}

function arxivCitation(arxivId) {
  return {
    key: `arxiv:${arxivId}`,
    sourceKind: 'arxiv',
    sourceId: arxivId,
    sourceLabel: `arXiv ${arxivId}`,
    url: `https://arxiv.org/abs/${arxivId}`,
  };
}

function doiCitation(doi) {
  return {
    key: `doi:${doi}`,
    sourceKind: 'doi',
    sourceId: doi,
    sourceLabel: `DOI ${doi}`,
    url: `https://doi.org/${doi}`,
  };
}

function canonicalUrl(raw) {
  try {
    const parsed = new URL(cleanTrailingPunctuation(raw));
    if (!['http:', 'https:'].includes(parsed.protocol)) return null;
    parsed.protocol = parsed.protocol.toLowerCase();
    parsed.hostname = parsed.hostname.toLowerCase().replace(/^www\./, '');
    parsed.hash = '';
    parsed.searchParams.sort();
    if (parsed.pathname !== '/') parsed.pathname = parsed.pathname.replace(/\/+$/, '');
    return parsed.toString();
  } catch {
    return null;
  }
}

function citationFromUrl(raw) {
  const canonical = canonicalUrl(raw);
  if (!canonical) return null;
  const parsed = new URL(canonical);
  const host = parsed.hostname.replace(/^www\./, '');

  if (host === 'arxiv.org' || host === 'export.arxiv.org' || host === 'ar5iv.org') {
    const pathMatch = parsed.pathname.match(/^\/(?:abs|pdf|html|e-print)\/(.+)$/i);
    const arxivId = pathMatch ? normalizeArxivId(decodeURIComponent(pathMatch[1])) : null;
    if (arxivId) return arxivCitation(arxivId);
  }

  if (host === 'doi.org' || host === 'dx.doi.org') {
    const doi = normalizeDoi(decodeURIComponent(parsed.pathname.replace(/^\/+/, '')));
    if (doi) return doiCitation(doi);
  }

  return {
    key: `url:${canonical}`,
    sourceKind: 'url',
    sourceId: canonical,
    sourceLabel: host,
    url: canonical,
  };
}

function isScholarlyUrl(citation) {
  if (!citation) return false;
  if (citation.sourceKind !== 'url') return true;
  const host = new URL(citation.url).hostname.replace(/^www\./, '');
  return SCHOLARLY_HOSTS.some((candidate) => host === candidate || host.endsWith(`.${candidate}`));
}

function priorWorkSection(markdown) {
  const lines = String(markdown || '').split(/\r?\n/);
  let level = null;
  const selected = [];
  for (const line of lines) {
    const heading = line.match(/^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$/);
    if (heading) {
      const name = heading[2].trim().toLowerCase().replace(/&/g, 'and');
      if (/^prior work(?:\s+and\s+provenance)?$/.test(name)) {
        level = heading[1].length;
        continue;
      }
      if (level !== null && heading[1].length <= level) break;
    }
    if (level !== null) selected.push(line);
  }
  return selected.join('\n');
}

function markdownLinks(markdown) {
  const links = [];
  const re = /(?<!!)\[([^\]\n]{1,300})\]\(\s*(https?:\/\/[^\s)]+)(?:\s+["'][^"'\n]*["'])?\s*\)/gi;
  for (const match of String(markdown || '').matchAll(re)) {
    links.push({ title: usableTitle(match[1]), url: match[2] });
  }
  return links;
}

function bareUrls(markdown) {
  return String(markdown || '').match(/https?:\/\/[^\s<>"'`]+/gi) || [];
}

/**
 * Extract portable paper references from an experiment plan.
 *
 * arXiv ids and DOI ids/URLs are recognized anywhere. URLs on known scholarly
 * hosts are also recognized anywhere. The Prior work & provenance section is
 * an explicit paper context, so any stable HTTP(S) source URL there is kept.
 */
export function extractPaperCitations(markdown) {
  // Templates carry example identifiers inside authoring comments. They are
  // instructions, not citations, even if an agent leaves the comments intact.
  const text = String(markdown || '').replace(/<!--[\s\S]*?-->/g, '');
  const provenance = priorWorkSection(text);
  const citations = new Map();

  const add = (citation, rawTitle = null) => {
    if (!citation) return;
    const title = usableTitle(rawTitle);
    const existing = citations.get(citation.key);
    if (!existing) {
      citations.set(citation.key, { ...citation, title });
    } else if (!existing.title && title) {
      citations.set(citation.key, { ...existing, title });
    }
  };

  const addLinks = (body, acceptAnySource) => {
    for (const link of markdownLinks(body)) {
      const citation = citationFromUrl(link.url);
      if (acceptAnySource || isScholarlyUrl(citation)) add(citation, link.title);
    }
    for (const rawUrl of bareUrls(body)) {
      const citation = citationFromUrl(rawUrl);
      if (acceptAnySource || isScholarlyUrl(citation)) add(citation);
    }
  };

  addLinks(text, false);
  addLinks(provenance, true);

  const quotedArxiv = new RegExp(
    `["“”']([^"“”'\\n]{3,300})["“”']\\s*[,;:(\\s-]{0,6}arxiv\\s*:\\s*(${ARXIV_ID_SOURCE})(?:v\\d+)?`,
    'gi',
  );
  for (const match of text.matchAll(quotedArxiv)) {
    const arxivId = normalizeArxivId(match[2]);
    if (arxivId) add(arxivCitation(arxivId), match[1]);
  }

  const doiRe = new RegExp(`(?:\\bdoi\\s*:\\s*)(${DOI_SOURCE})`, 'gi');
  for (const match of text.matchAll(doiRe)) {
    const doi = normalizeDoi(match[1]);
    if (doi) add(doiCitation(doi));
  }

  // The prefix is captured instead of using lookbehind so this remains safe
  // for every browser target supported by the UI build.
  const arxivRe = new RegExp(
    `(?:^|[^a-z0-9./])(${ARXIV_ID_SOURCE})(?:v\\d+)?(?![a-z0-9.])`,
    'gi',
  );
  for (const match of text.matchAll(arxivRe)) {
    const arxivId = normalizeArxivId(match[1]);
    if (arxivId) add(arxivCitation(arxivId));
  }

  return [...citations.values()];
}
