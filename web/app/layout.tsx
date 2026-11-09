import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "AlphaStock",
  description: "AI-energy and Physical-AI signal research, in English and Chinese.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
