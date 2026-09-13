import type { Metadata } from "next";
import { Inter, Noto_Sans_Bengali } from "next/font/google";
import { MotionConfig } from "framer-motion";
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

export const metadata: Metadata = {
  title: "Textbook Tutor",
  description:
    "A grade-adaptive tutor chatbot that answers only from your uploaded textbook.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      className={`${inter.variable} ${notoSansBengali.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col">
        {/* reducedMotion="user" makes every Framer Motion animation in the
            app respect the OS's prefers-reduced-motion setting automatically. */}
        <MotionConfig reducedMotion="user">
          <AuthProvider>
            {children}
            <Toaster />
          </AuthProvider>
        </MotionConfig>
      </body>
    </html>
  );
}
