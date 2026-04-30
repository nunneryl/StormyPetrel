'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';
import { usePathname } from 'next/navigation';
import { Logo } from './Logo';

const NAV_LINKS = [
  { href: '/map', label: 'Map' },
  { href: '/regions', label: 'Regions' },
  { href: '/blog', label: 'Blog' },
];

export function SiteNav() {
  const [mobileOpen, setMobileOpen] = useState(false);
  const pathname = usePathname();

  // Close the mobile menu whenever the route changes — without this, a
  // tap-link doesn't dismiss the overlay.
  useEffect(() => {
    setMobileOpen(false);
  }, [pathname]);

  // Lock background scroll when the mobile menu is open so the page
  // behind doesn't slide around under the user's finger.
  useEffect(() => {
    if (mobileOpen) {
      document.body.style.overflow = 'hidden';
    } else {
      document.body.style.overflow = '';
    }
    return () => {
      document.body.style.overflow = '';
    };
  }, [mobileOpen]);

  return (
    <header className="sticky top-0 z-30 border-b border-ink-600 bg-ink-900/80 backdrop-blur supports-[backdrop-filter]:bg-ink-900/70">
      <div className="mx-auto max-w-7xl px-4 h-14 flex items-center justify-between">
        <Link href="/" className="flex items-center" aria-label="Stormy Petrel home">
          <Logo />
        </Link>

        {/* Desktop nav */}
        <nav className="hidden sm:flex items-center gap-1 text-sm">
          {NAV_LINKS.map((l) => (
            <NavLink key={l.href} href={l.href} pathname={pathname}>
              {l.label}
            </NavLink>
          ))}
        </nav>

        {/* Mobile hamburger trigger */}
        <button
          type="button"
          aria-label={mobileOpen ? 'Close menu' : 'Open menu'}
          aria-expanded={mobileOpen}
          onClick={() => setMobileOpen((v) => !v)}
          className="sm:hidden inline-flex items-center justify-center w-11 h-11 -mr-2 rounded-md text-text-primary hover:bg-ink-700"
        >
          {mobileOpen ? <CloseIcon /> : <BurgerIcon />}
        </button>
      </div>

      {/* Mobile menu overlay */}
      {mobileOpen && (
        <div className="sm:hidden border-t border-ink-600 bg-ink-900">
          <nav className="px-4 py-2 flex flex-col">
            {NAV_LINKS.map((l) => (
              <Link
                key={l.href}
                href={l.href}
                className={`px-2 py-3 text-base rounded-md ${
                  pathname === l.href || pathname?.startsWith(`${l.href}/`)
                    ? 'text-cyan-400 bg-ink-800'
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
          ? 'text-cyan-400'
          : 'text-text-secondary hover:text-text-primary hover:bg-ink-700/50'
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
