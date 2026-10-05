"use client";

import { memo } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";

import { cn } from "@/lib/utils";

/**
 * Tutor text → markdown with LaTeX (AC-5.6).
 *
 * Models write math as `$…$`, `$$…$$`, `\(…\)` or `\[…\]`; remark-math understands the
 * dollar forms, so the bracket forms are rewritten (outside code). Raw HTML is never
 * rendered (react-markdown escapes it; no rehype-raw) and KaTeX runs with `trust: false`.
 */
const FENCE = /(```[\s\S]*?(?:```|$)|`[^`\n]*`)/g;

export function normaliseMath(text: string): string {
  return text
    .split(FENCE)
    .map((part, i) =>
      i % 2 === 1
        ? part
        : part
            .replace(/\\\[([\s\S]+?)\\\]/g, (_, body: string) => `\n$$\n${body.trim()}\n$$\n`)
            .replace(/\\\(([\s\S]+?)\\\)/g, (_, body: string) => `$${body.trim()}$`)
            // `$$…$$` alone on a line is meant as display math (remark-math reads it as inline).
            .replace(/^[ \t]*\$\$([^\n$]+?)\$\$[ \t]*$/gm, (_, body: string) => `$$\n${body.trim()}\n$$`),
    )
    .join("");
}

/** Element props without react-markdown's `node` (not a DOM attribute). */
function domProps<T extends { node?: unknown }>(props: T): Omit<T, "node"> {
  const rest: Partial<T> = { ...props };
  delete rest.node;
  return rest as Omit<T, "node">;
}

const components: Components = {
  a: (props) => (
    <a {...domProps(props)} target="_blank" rel="noopener noreferrer" className="text-brand-700 underline" />
  ),
  code: (props) => (
    <code
      {...domProps(props)}
      className={cn("rounded bg-slate-100 px-1 py-0.5 text-[0.9em]", props.className)}
    />
  ),
  pre: (props) => (
    <pre
      {...domProps(props)}
      className="overflow-x-auto rounded-lg bg-slate-900 p-3 text-slate-100 [&_code]:bg-transparent"
    />
  ),
};

export const Markdown = memo(function Markdown({
  children,
  className,
}: {
  children: string;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "space-y-2 text-sm leading-relaxed text-slate-800 [&_ol]:list-decimal [&_ol]:pl-5 [&_ul]:list-disc [&_ul]:pl-5",
        "[&_.katex-display]:overflow-x-auto [&_.katex-display]:py-1 [&_h1]:font-semibold [&_h2]:font-semibold [&_h3]:font-semibold",
        className,
      )}
    >
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[[rehypeKatex, { throwOnError: false, strict: "ignore", trust: false }]]}
        components={components}
      >
        {normaliseMath(children)}
      </ReactMarkdown>
    </div>
  );
});
