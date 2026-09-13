import { BookOpen, ShieldCheck, Sparkles } from "lucide-react";
import { Logo } from "@/components/logo";

const FEATURES = [
  { icon: BookOpen, text: "Answers grounded in your own textbook, page by page" },
  { icon: Sparkles, text: "Explained the way your class actually teaches it" },
  { icon: ShieldCheck, text: "Every answer double-checked before you see it" },
];

export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex flex-1">
      {/* Branded panel -- hidden below md, where the small logo above the
          form (rendered further down) carries the brand instead. */}
      <div className="relative hidden w-1/2 items-center justify-center overflow-hidden bg-gradient-to-br from-indigo-600 to-indigo-900 p-12 text-white md:flex">
        <div className="absolute -top-24 -left-24 size-96 rounded-full bg-amber-400/20 blur-3xl" />
        <div className="absolute -bottom-32 -right-16 size-96 rounded-full bg-indigo-400/30 blur-3xl" />
        <div className="relative flex max-w-sm flex-col gap-8">
          <div className="flex items-center gap-3">
            <div className="flex size-11 shrink-0 items-center justify-center rounded-xl bg-white/15 backdrop-blur-sm">
              <BookOpen className="size-6" />
            </div>
            <span className="font-heading text-2xl font-semibold tracking-tight">Textbook Tutor</span>
          </div>
          <div>
            <h1 className="text-3xl leading-tight font-semibold tracking-tight">
              Answers straight from your textbook
            </h1>
            <p className="mt-3 text-indigo-100">
              Upload your class book and ask anything -- every answer is checked against the page
              it came from before it ever reaches you.
            </p>
          </div>
          <ul className="flex flex-col gap-4">
            {FEATURES.map(({ icon: Icon, text }) => (
              <li key={text} className="flex items-start gap-3 text-sm text-indigo-50">
                <div className="mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-full bg-white/15">
                  <Icon className="size-3.5" />
                </div>
                {text}
              </li>
            ))}
          </ul>
        </div>
      </div>

      <div className="flex flex-1 items-center justify-center bg-gradient-to-br from-indigo-50 via-background to-amber-50 p-6 dark:from-indigo-950/40 dark:via-background dark:to-amber-950/20 md:w-1/2 md:flex-none md:bg-none">
        <div className="w-full max-w-md">
          <div className="mb-6 flex justify-center md:hidden">
            <Logo />
          </div>
          {children}
        </div>
      </div>
    </div>
  );
}
