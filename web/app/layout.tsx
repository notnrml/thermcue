import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import "./globals.css";

const inter = Inter({
  subsets: ["latin"],
  variable: "--font-inter",
});

const jetbrainsMono = JetBrains_Mono({
  subsets: ["latin"],
  variable: "--font-jetbrains-mono",
});

export const metadata: Metadata = {
  title: "ThermCue",
  description:
    "Temperature-aware operations planning for outdoor events, powered by FortyGuard.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en-GB" className="dark">
      <body
        className={`${inter.variable} ${jetbrainsMono.variable} min-w-[1280px] bg-base-bg font-sans text-body text-base-text antialiased`}
      >
        {children}
      </body>
    </html>
  );
}
