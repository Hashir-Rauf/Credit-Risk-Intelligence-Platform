import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "Credit Risk Intelligence Platform",
  description: "Coursework demo: score a credit-card customer with the production model.",
};

const NAV = [
  { href: "/", label: "Score a customer" },
  { href: "/model", label: "Model & data" },
  { href: "/monitoring", label: "Monitoring" },
  { href: "/admin", label: "Admin" },
];

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>
        <header className="topbar">
          <div className="shell">
            <Link href="/" className="brand">
              Credit Risk Intelligence
            </Link>
            <nav className="nav" aria-label="Main">
              {NAV.map((item) => (
                <Link key={item.href} href={item.href}>
                  {item.label}
                </Link>
              ))}
            </nav>
          </div>
        </header>
        <div className="notice" role="note">
          <div className="shell">
            <strong>Not a real lending decision tool.</strong> This is a university course project. The model&apos;s
            score ranks customers; it is not a calibrated probability of default.
          </div>
        </div>
        <main className="shell">{children}</main>
      </body>
    </html>
  );
}
