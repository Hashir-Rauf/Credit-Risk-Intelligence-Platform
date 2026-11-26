"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

type Item = { href: string; label: string; icon: React.ReactNode };

const stroke = { fill: "none", stroke: "currentColor", strokeWidth: 1.8, strokeLinecap: "round", strokeLinejoin: "round" } as const;

const ITEMS: Item[] = [
  {
    href: "/",
    label: "Score a customer",
    icon: (
      <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true" {...stroke}>
        <path d="M4 20V10M10 20V4M16 20v-7M22 20H2" />
      </svg>
    ),
  },
  {
    href: "/model",
    label: "Model & data",
    icon: (
      <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true" {...stroke}>
        <ellipse cx="12" cy="6" rx="8" ry="3" />
        <path d="M4 6v6c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6" />
      </svg>
    ),
  },
  {
    href: "/monitoring",
    label: "Monitoring",
    icon: (
      <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true" {...stroke}>
        <path d="M3 12h4l3-8 4 16 3-8h4" />
      </svg>
    ),
  },
  {
    href: "/admin",
    label: "Admin",
    icon: (
      <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true" {...stroke}>
        <path d="M12 3l8 3v6c0 4.5-3.2 8-8 9-4.8-1-8-4.5-8-9V6l8-3z" />
        <path d="M9 12l2 2 4-4" />
      </svg>
    ),
  },
];

export function titleFor(pathname: string): string {
  const match = ITEMS.find((item) => (item.href === "/" ? pathname === "/" : pathname.startsWith(item.href)));
  return match ? match.label : "Credit Risk Intelligence";
}

export function Sidebar() {
  const pathname = usePathname() ?? "/";
  return (
    <aside className="sidebar">
      <Link href="/" className="brand">
        <span className="brand-mark" aria-hidden="true">
          <svg viewBox="0 0 24 24" width="20" height="20" {...stroke}>
            <path d="M12 3l8 3v6c0 4.5-3.2 8-8 9-4.8-1-8-4.5-8-9V6l8-3z" />
          </svg>
        </span>
        <span className="brand-text">
          Credit Risk
          <small>Intelligence Platform</small>
        </span>
      </Link>
      <nav className="nav" aria-label="Main">
        {ITEMS.map((item) => {
          const active = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
          return (
            <Link key={item.href} href={item.href} aria-current={active ? "page" : undefined}>
              {item.icon}
              <span>{item.label}</span>
            </Link>
          );
        })}
      </nav>
      <div className="sidebar-foot">University course project</div>
    </aside>
  );
}

export function PageTitle() {
  const pathname = usePathname() ?? "/";
  return <span className="crumb">{titleFor(pathname)}</span>;
}
