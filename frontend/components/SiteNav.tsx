'use client';

import Link from 'next/link';
import Image from 'next/image';
import { useEffect, useState } from 'react';
import { flushSync } from 'react-dom';
import { usePathname } from 'next/navigation';
import { NavSearch } from './NavSearch';
import { useSpotSearchList } from './SpotSearchList';

const NAV_LINKS = [
  { href: '/map', label: 'Map' },
  { href: '/cams', label: 'Cams' },
  { href: '/reports', label: 'Reports' },
  { href: '/learn', label: 'Learn' },
  { href: '/regions', label: 'Regions' },
  { href: '/blog', label: 'Blog' },
  { href: '/about', label: 'About' },
];

export function SiteNav() {
  // The one shared list (components/SpotSearchList), provided by the root layout.
  const hasSearch = useSpotSearchList().length > 0;
  const [mobileOpen, setMobileOpen] = useState(false);
  // On a phone the header's search button opens this panel, focused, without the menu.
  const [searchOpen, setSearchOpen] = useState(false);
  const pathname = usePathname();

  useEffect(() => {
    setMobileOpen(false);
    setSearchOpen(false);
  }, [pathname]);

  useEffect(() => {
    if (mobileOpen) document.body.style.overflow = 'hidden';
    else document.body.style.overflow = '';
    return () => {
      document.body.style.overflow = '';
    };
  }, [mobileOpen]);

  return (
    <header className="sticky top-0 z-30 bg-white border-b border-ink-600">
      <div className="mx-auto max-w-7xl px-4 h-14 flex items-center justify-between gap-4">
        <Link
          href="/"
          className="flex items-center shrink-0"
          aria-label="Stormy Petrel home"
        >
          {/* 53x64 source, 2x the 26x32 it is shown at (public/brand/sp-initials-nav.png, cut from
              sp-initials.png), served through the image optimizer. width/height are the shown
              size, so the space is held before it loads. */}
          <Image
            src="/brand/sp-initials-nav.png"
            alt="Stormy Petrel"
            width={26}
            height={32}
            priority
          />
        </Link>

        <div className="hidden md:flex flex-1 justify-center max-w-xl">
          {hasSearch && <NavSearch />}
        </div>

        <nav className="hidden sm:flex items-center gap-1 text-sm">
          {NAV_LINKS.map((l) => (
            <NavLink key={l.href} href={l.href} pathname={pathname}>
              {l.label}
            </NavLink>
          ))}
        </nav>

        <div className="sm:hidden flex items-center -mr-2">
          {hasSearch && (
            <button
              type="button"
              aria-label={searchOpen ? 'Close search' : 'Search spots'}
              aria-expanded={searchOpen}
              data-mobile-search-button
              onClick={() => {
                const opening = !searchOpen;
                // Render the panel now, inside the tap, and focus its input in the same tap: iOS
                // only raises the keyboard for a focus made within the user's gesture.
                flushSync(() => { setSearchOpen(opening); setMobileOpen(false); });
                if (opening) document.querySelector<HTMLInputElement>('[data-mobile-search-panel] input')?.focus();
              }}
              className="inline-flex items-center justify-center w-11 h-11 rounded-md text-text-primary hover:bg-ink-800"
            >
              {searchOpen ? <CloseIcon /> : <SearchGlyph />}
            </button>
          )}
          <button
            type="button"
            aria-label={mobileOpen ? 'Close menu' : 'Open menu'}
            aria-expanded={mobileOpen}
            onClick={() => { setMobileOpen((v) => !v); setSearchOpen(false); }}
            className="inline-flex items-center justify-center w-11 h-11 rounded-md text-text-primary hover:bg-ink-800"
          >
            {mobileOpen ? <CloseIcon /> : <BurgerIcon />}
          </button>
        </div>
      </div>

      {searchOpen && (
        <div className="sm:hidden border-t border-ink-600 bg-white px-4 py-3" data-mobile-search-panel>
          <NavSearch light autoFocus />
        </div>
      )}

      {mobileOpen && (
        <div className="sm:hidden border-t border-ink-600 bg-white">
          {hasSearch && (
            <div className="px-4 py-3 border-b border-ink-600">
              <NavSearch light />
            </div>
          )}
          <nav className="px-4 py-2 flex flex-col">
            {NAV_LINKS.map((l) => (
              <Link
                key={l.href}
                href={l.href}
                className={`px-2 py-3 text-base rounded-md ${
                  pathname === l.href || pathname?.startsWith(`${l.href}/`)
                    ? 'text-cyan-600 bg-ink-800'
                    : 'text-text-primary hover:bg-ink-800'
                }`}
              >
                {l.label}
              </Link>
            ))}
          </nav>
        </div>
      )}
    </header>
  );
}

function NavLink({
  href,
  pathname,
  children,
}: {
  href: string;
  pathname: string | null;
  children: React.ReactNode;
}) {
  const active = pathname === href || pathname?.startsWith(`${href}/`);
  return (
    <Link
      href={href}
      className={`px-3 py-1.5 rounded-md transition ${
        active
          ? 'text-cyan-600'
          : 'text-text-secondary hover:text-text-primary hover:bg-ink-800'
      }`}
    >
      {children}
    </Link>
  );
}

function SearchGlyph() {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <circle cx="11" cy="11" r="7" />
      <path d="M21 21l-4.3-4.3" />
    </svg>
  );
}

function BurgerIcon() {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden>
      <path d="M4 7h16M4 12h16M4 17h16" />
    </svg>
  );
}

function CloseIcon() {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden>
      <path d="M6 6l12 12M6 18L18 6" />
    </svg>
  );
}
