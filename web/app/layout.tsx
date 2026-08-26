import type { Metadata, Viewport } from "next";
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

/**
 * Render at the design width and let the device scale to fit.
 *
 * The design brief is explicit that this is a desktop control room: 1440x900,
 * one responsive pass at 1280, no mobile. That stands. But the submission rule
 * is equally explicit that the public demo must load on a phone, and with
 * Next's default `width=device-width` against a body pinned to `min-w-[1280px]`
 * a phone rendered the top-left corner of the map and nothing else. The right
 * rail - Compare, Agent, Drivers, Validation, which is the entire argument -
 * was off-screen with no scroll affordance to suggest it existed.
 *
 * Declaring the viewport at the layout width makes the phone shrink the whole
 * control room to fit and lets the reader pinch to read it. That is not a
 * mobile redesign and does not pretend to be one; it is the difference between
 * a judge seeing the product and seeing a corner of a map.
 */
export const viewport: Viewport = {
  width: 1280,
  initialScale: 0.3,
  minimumScale: 0.25,
  maximumScale: 5,
  userScalable: true,
  themeColor: "#0B1220",
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
