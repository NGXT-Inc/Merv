/**
 * ProviderIcon — the provider's real logo mark in a small tile.
 *
 * Logo bitmaps live in public/providers/<name>.png (64px marks fetched from
 * each vendor's own site), so they ship with the UI build — no external
 * requests at render time. The tile keeps a soft neutral wash so light-on-
 * transparent marks stay readable in both themes; a missing file falls back
 * to a two-letter monogram rather than a broken image.
 */

import { useState } from 'react';

const LABELS = {
  lambda_labs: 'LL',
  thunder_compute: 'TC',
  hyperstack: 'HS',
  digitalocean: 'DO',
  verda: 'VD',
  voltage_park: 'VP',
  tensordock: 'TD',
  aws: 'AWS',
  gcp: 'GC',
  azure: 'AZ',
};

export default function ProviderIcon({ provider, size = 30 }) {
  const [broken, setBroken] = useState(false);
  const src = `${import.meta.env.BASE_URL}providers/${provider}.png`;
  return (
    <span className="sbxp-icon" style={{ width: size, height: size }} aria-hidden="true">
      {broken ? (
        <span className="sbxp-icon-fallback">{LABELS[provider] || '?'}</span>
      ) : (
        <img
          src={src}
          width={size - 8}
          height={size - 8}
          alt=""
          onError={() => setBroken(true)}
        />
      )}
    </span>
  );
}
