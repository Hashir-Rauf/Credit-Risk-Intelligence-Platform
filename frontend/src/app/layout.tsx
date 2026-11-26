import type { Metadata } from "next";
import "./globals.css";
import { PageTitle, Sidebar } from "@/components/Sidebar";

export const metadata: Metadata = {
  title: "Credit Risk Intelligence Platform",
  description: "Coursework demo: score a credit-card customer with the production model.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>
        <div className="app">
          <Sidebar />
          <div className="content">
            <header className="topbar">
              <PageTitle />
              <span className="pill">Educational model</span>
            </header>
            <div className="notice" role="note">
              <strong>Not a real lending decision tool.</strong> This is a university course project. The model&apos;s score
              ranks customers; it is not a calibrated probability of default.
            </div>
            <main className="main">{children}</main>
          </div>
        </div>
      </body>
    </html>
  );
}
