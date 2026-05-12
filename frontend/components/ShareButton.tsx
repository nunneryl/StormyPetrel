'use client';

import { useState } from 'react';

// Compact share button — tries the native share sheet first (mobile,
// where it's a much nicer experience than copy-link), falls back to
// clipboard with a brief "Link copied" toast on desktop. Caller passes
// `url` as a path-style string; we expand it to absolute at click time
// using window.location so the share target is the actual page URL.

type Props = {
  url: string;
  title?: string;
  text?: string;
  className?: string;
};

export function ShareButton({ url, title, text, className = '' }: Props) {
  const [toast, setToast] = useState<string | null>(null);

  async function onClick(e: React.MouseEvent<HTMLButtonElement>) {
    // The button often sits inside a Link wrapper; stop the event so
    // the click doesn't navigate away from the page.
    e.preventDefault();
    e.stopPropagation();

    const absolute = url.startsWith('http')
      ? url
      : `${window.location.origin}${url}`;

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const nav: any = navigator;
    if (typeof nav.share === 'function') {
      try {
        await nav.share({ url: absolute, title, text });
        return;
      } catch {
        // Share was cancelled or failed — fall through to clipboard.
      }
    }

    try {
      await navigator.clipboard.writeText(absolute);
      setToast('Link copied');
      setTimeout(() => setToast(null), 1800);
    } catch {
      setToast('Copy failed');
      setTimeout(() => setToast(null), 1800);
    }
  }

  return (
    <span className={`relative inline-block ${className}`}>
      <button
        type="button"
        onClick={onClick}
        aria-label="Share this report"
        className="inline-flex items-center justify-center w-7 h-7 rounded-md text-text-secondary hover:text-cyan-600 hover:bg-ink-800 transition"
      >
        <ShareIcon />
      </button>
      {toast && (
        <span
          role="status"
          className="absolute right-0 top-9 z-20 whitespace-nowrap rounded-md bg-text-primary text-white text-[11px] font-bold px-2 py-1 shadow-card"
        >
          {toast}
        </span>
      )}
    </span>
  );
}

function ShareIcon() {
  return (
    <svg
      width="15"
      height="15"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <circle cx="18" cy="5" r="3" />
      <circle cx="6" cy="12" r="3" />
      <circle cx="18" cy="19" r="3" />
      <line x1="8.59" y1="13.51" x2="15.42" y2="17.49" />
      <line x1="15.41" y1="6.51" x2="8.59" y2="10.49" />
    </svg>
  );
}
