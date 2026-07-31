import assert from 'node:assert/strict';
import test from 'node:test';

import { extractPaperCitations } from './paperCitations.js';

test('normalizes and deduplicates modern arXiv references', () => {
  const citations = extractPaperCitations(`
## Prior work & provenance
[Attention Is All You Need](https://arxiv.org/pdf/1706.03762v7.pdf)
The design also follows arXiv:1706.03762v2.
`);
  assert.deepEqual(citations, [{
    key: 'arxiv:1706.03762',
    sourceKind: 'arxiv',
    sourceId: '1706.03762',
    sourceLabel: 'arXiv 1706.03762',
    url: 'https://arxiv.org/abs/1706.03762',
    title: 'Attention Is All You Need',
  }]);
});

test('accepts legacy arXiv ids and source-native DOI forms', () => {
  const citations = extractPaperCitations(`
See arXiv:hep-th/9901001v2 and doi:10.1145/3290605.3300233.
The DOI is also https://doi.org/10.1145/3290605.3300233.
`);
  assert.deepEqual(citations.map((citation) => citation.key), [
    'doi:10.1145/3290605.3300233',
    'arxiv:hep-th/9901001',
  ]);
});

test('uses provenance as context for a publisher-specific stable URL', () => {
  const citations = extractPaperCitations(`
## Method
Implementation: https://example.com/source-code

## Prior work and provenance
[Publisher copy](https://publisher.example/papers/answer?edition=2#abstract)

## Evaluation
No more references.
`);
  assert.equal(citations.length, 1);
  assert.equal(citations[0].key, 'url:https://publisher.example/papers/answer?edition=2');
  assert.equal(citations[0].title, 'Publisher copy');
});

test('recognizes scholarly-host links outside provenance without treating every URL as a paper', () => {
  const citations = extractPaperCitations(`
[Relevant result](https://openreview.net/forum?id=abc123)
Code lives at https://github.com/example/repository.
An internal paper_123456789abc id is not portable.
`);
  assert.deepEqual(citations.map((citation) => citation.key), [
    'url:https://openreview.net/forum?id=abc123',
  ]);
});

test('ignores source-id examples left in template comments', () => {
  const citations = extractPaperCitations(`
## Prior work & provenance
<!-- Use arXiv:2401.12345, doi:10.1145/3290605.3300233, or a stable URL. -->
No prior work materially informed this design.
`);
  assert.deepEqual(citations, []);
});
