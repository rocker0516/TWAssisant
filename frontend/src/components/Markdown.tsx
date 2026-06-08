import ReactMarkdown from "react-markdown";

// LLM 輸出 markdown 的精簡樣式（無 typography plugin，手動映射）
export function Markdown({ children }: { children: string }) {
  return (
    <div className="text-sm leading-relaxed text-gray-200">
      <ReactMarkdown
        components={{
          h1: (p) => <h1 className="mb-2 mt-1 text-base font-bold text-white" {...p} />,
          h2: (p) => <h2 className="mb-1.5 mt-3 text-sm font-bold text-sky-200" {...p} />,
          h3: (p) => <h3 className="mb-1 mt-2 text-sm font-semibold text-gray-100" {...p} />,
          p: (p) => <p className="mb-2" {...p} />,
          ul: (p) => <ul className="mb-2 ml-4 list-disc space-y-0.5" {...p} />,
          ol: (p) => <ol className="mb-2 ml-4 list-decimal space-y-0.5" {...p} />,
          strong: (p) => <strong className="font-semibold text-white" {...p} />,
          hr: () => <hr className="my-2 border-edge" />,
          blockquote: (p) => <blockquote className="border-l-2 border-edge pl-3 text-muted" {...p} />,
          table: (p) => <table className="my-2 w-full border-collapse text-xs" {...p} />,
          th: (p) => <th className="border border-edge px-2 py-1 text-left" {...p} />,
          td: (p) => <td className="border border-edge px-2 py-1" {...p} />,
          code: (p) => <code className="rounded bg-panel2 px-1 text-xs" {...p} />,
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
