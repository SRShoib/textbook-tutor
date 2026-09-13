import type { Metadata } from "next";
import { Fraunces, Inter, Lexend, Noto_Sans_Bengali } from "next/font/google";
import { MotionConfig } from "framer-motion";
import { ThemeProvider } from "next-themes";
import { Toaster } from "@/components/ui/sonner";
import { AuthProvider } from "@/lib/auth-context";
import "./globals.css";

// Latin UI face. Falls through to Noto Sans Bengali below for any glyph
// Inter doesn't cover -- stage 2 answers mix English body text with a
// Bangla-script question restatement (style_guide.md §3.4), so both scripts
// need to render correctly in the same sentence.
const inter = Inter({
  variable: "--font-inter",
  subsets: ["latin"],
});

const notoSansBengali = Noto_Sans_Bengali({
  variable: "--font-noto-bengali",
  subsets: ["bengali"],
});

// Display serif for the wordmark and card titles only (--font-heading in
// globals.css) -- a deliberate contrast against Inter's body text, the
// classic serif-display/sans-body pairing, and its warm literary character
// fits a "textbook" brand better than another geometric sans would.
const fraunces = Fraunces({
  variable: "--font-fraunces",
  subsets: ["latin"],
  weight: ["500", "600", "700"],
});

// Reading face for chat content only (--font-reading in globals.css) --
// Lexend is a humanist sans specifically engineered (and studied) to raise
// reading proficiency and reduce visual stress, tuned via x-height/spacing
// rather than a purely aesthetic choice. This app's whole purpose is a
// Class 5 student reading explanations, so the chat transcript is exactly
// the text that benefits most; falls through to Noto Sans Bengali for the
// same mixed-script reason as --font-sans above.
const lexend = Lexend({
  variable: "--font-lexend",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Textbook Tutor",
  description:
    "A grade-adaptive tutor chatbot that answers only from your uploaded textbook.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      // suppressHydrationWarning: next-themes sets the .dark/.light class on
      // this element from an inline script before React hydrates, which
      // otherwise mismatches the server-rendered markup on the first paint.
      suppressHydrationWarning
      className={`${inter.variable} ${notoSansBengali.variable} ${fraunces.variable} ${lexend.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col">
        {/* reducedMotion="user" makes every Framer Motion animation in the
            app respect the OS's prefers-reduced-motion setting automatically. */}
        <MotionConfig reducedMotion="user">
          <ThemeProvider attribute="class" defaultTheme="system" enableSystem disableTransitionOnChange>
            <AuthProvider>
              {children}
              <Toaster />
            </AuthProvider>
          </ThemeProvider>
        </MotionConfig>
      </body>
    </html>
  );
}
