'use client';

import Link from 'next/link';
import Image from 'next/image';
import { useEffect, useState } from 'react';
import { usePathname } from 'next/navigation';
import { NavSearch } from './NavSearch';

const NAV_LINKS = [
  { href: '/map', label: 'Map' },
  { href: '/cams', label: 'Cams' },
  { href: '/reports', label: 'Reports' },
  { href: '/learn', label: 'Learn' },
  { href: '/regions', label: 'Regions' },
  { href: '/blog', label: 'Blog' },
  { href: '/about', label: 'About' },
];

export type SpotSearchItem = {
  slug: string;
  name: string;
  state: string | null;
};

export function SiteNav({
  searchSpots = [],
}: {
  searchSpots?: SpotSearchItem[];
}) {
  const [mobileOpen, setMobileOpen] = useState(false);
  const pathname = usePathname();

  useEffect(() => {
    setMobileOpen(false);
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
          <Image
            src="/brand/sp-initials.png"
            alt="Stormy Petrel"
            width={32}
            height={32}
            priority
            unoptimized
            style={{ height: 32, width: 'auto' }}
          />
        </Link>

        <div className="hidden md:flex flex-1 justify-center max-w-xl">
          {searchSpots.length > 0 && <NavSearch spots={searchSpots} />}
        </div>

        <nav className="hidden sm:flex items-center gap-1 text-sm">
          {NAV_LINKS.map((l) => (
            <NavLink key={l.href} href={l.href} pathname={pathname}>
              {l.label}
            </NavLink>
          ))}
        </nav>

        <button
          type="button"
          aria-label={mobileOpen ? 'Close menu' : 'Open menu'}
          aria-expanded={mobileOpen}
          onClick={() => setMobileOpen((v) => !v)}
          className="sm:hidden inline-flex items-center justify-center w-11 h-11 -mr-2 rounded-md text-text-primary hover:bg-ink-800"
        >
          {mobileOpen ? <CloseIcon /> : <BurgerIcon />}
        </button>
      </div>

      {mobileOpen && (
        <div className="sm:hidden border-t border-ink-600 bg-white">
          {searchSpots.length > 0 && (
            <div className="px-4 py-3 border-b border-ink-600">
              <NavSearch spots={searchSpots} light />
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
