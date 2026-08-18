import { memo, useEffect, useMemo, useState } from 'react';
import { Copy, Check } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { Components } from 'react-markdown';
import type { ReactNode } from 'react';

interface CodeBlockProps {
  code: string;
  language?: string;
}

type SupportedLanguage =
  | 'text'
  | 'javascript'
  | 'typescript'
  | 'tsx'
  | 'jsx'
  | 'python'
  | 'bash'
  | 'json'
  | 'markdown'
  | 'css'
  | 'html'
  | 'diff'
  | 'yaml'
  | 'xml';

type CodeToHtml = (
  code: string,
  options: {
    lang: SupportedLanguage;
    themes: { light: 'github-light'; dark: 'github-dark' };
    defaultColor: 'light';
  },
) => Promise<string>;

let codeToHtmlLoader: Promise<CodeToHtml> | null = null;

function loadCodeToHtml(): Promise<CodeToHtml> {
  codeToHtmlLoader ??= Promise.all([
    import('shiki/core'),
    import('shiki/engine/javascript'),
  ]).then(([core, engine]) => {
    const createHighlighter = core.createBundledHighlighter({
      langs: {
        javascript: () => import('shiki/langs/javascript'),
        typescript: () => import('shiki/langs/typescript'),
        tsx: () => import('shiki/langs/tsx'),
        jsx: () => import('shiki/langs/jsx'),
        python: () => import('shiki/langs/python'),
        bash: () => import('shiki/langs/bash'),
        json: () => import('shiki/langs/json'),
        markdown: () => import('shiki/langs/markdown'),
        css: () => import('shiki/langs/css'),
        html: () => import('shiki/langs/html'),
        diff: () => import('shiki/langs/diff'),
        yaml: () => import('shiki/langs/yaml'),
        xml: () => import('shiki/langs/xml'),
      },
      themes: {
        'github-light': () => import('shiki/themes/github-light'),
        'github-dark': () => import('shiki/themes/github-dark'),
      },
      engine: () => engine.createJavaScriptRegexEngine(),
    });
    return core.createSingletonShorthands(createHighlighter).codeToHtml as unknown as CodeToHtml;
  });
  return codeToHtmlLoader;
}

function languageFor(language?: string): SupportedLanguage {
  if (!language) return 'text';
  const normalized = language.trim().toLowerCase();
  if (normalized === 'js') return 'javascript';
  if (normalized === 'ts') return 'typescript';
  if (normalized === 'tsx') return 'tsx';
  if (normalized === 'jsx') return 'jsx';
  if (normalized === 'py') return 'python';
  if (normalized === 'shell' || normalized === 'sh' || normalized === 'bash' || normalized === 'zsh') return 'bash';
  if (normalized === 'md' || normalized === 'mdx') return 'markdown';
  if (normalized === 'yml') return 'yaml';
  if (
    normalized === 'json' ||
    normalized === 'markdown' ||
    normalized === 'css' ||
    normalized === 'html' ||
    normalized === 'diff' ||
    normalized === 'yaml' ||
    normalized === 'xml'
  ) {
    return normalized;
  }
  return 'text';
}

function CodeBlock({ code, language = 'text' }: CodeBlockProps) {
  const [copied, setCopied] = useState(false);
  const [html, setHtml] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const lang = languageFor(language);
    loadCodeToHtml()
      .then((highlight) =>
        highlight(code.replace(/\n$/, ''), {
          lang,
          themes: {
            light: 'github-light',
            dark: 'github-dark',
          },
          defaultColor: 'light',
        }),
      )
      .then((result) => {
        if (!cancelled) setHtml(result);
      })
      .catch(() => {
        if (!cancelled) setHtml(null);
      });
    return () => {
      cancelled = true;
    };
  }, [code, language]);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch {
      // clipboard unavailable
    }
  };

  const langLabel = language || 'text';

  return (
    <div className="markdown-code">
      <div className="markdown-code__header">
        <span className="markdown-code__lang">{langLabel}</span>
        <button type="button" className="markdown-code__copy" onClick={copy} aria-label="Copy code">
          {copied ? <Check size={13} /> : <Copy size={13} />}
          {copied ? '已复制' : '复制'}
        </button>
      </div>
      {html ? (
        <div className="markdown-code__body" dangerouslySetInnerHTML={{ __html: html }} />
      ) : (
        <pre className="markdown-code__fallback">
          <code>{code}</code>
        </pre>
      )}
    </div>
  );
}

interface InlineCodeProps {
  children: ReactNode;
}

function InlineCode({ children }: InlineCodeProps) {
  return <code className="markdown-inline-code">{children}</code>;
}

interface MarkdownContentProps {
  content: string;
}

export const MarkdownContent = memo(function MarkdownContent({ content }: MarkdownContentProps) {
  const components: Components = useMemo(
    () => ({
      code(props) {
        const { className, children } = props;
        const match = /language-([\w-]+)/.exec(className || '');
        const text = String(children ?? '').replace(/\n$/, '');
        // A fenced code block is multi-line even when it has no language tag,
        // while inline code is a single token. Detecting by language class alone
        // wrongly rendered language-less fenced blocks (e.g. the H3 prompt
        // block) as inline <code>, which react-markdown then wrapped in a <pre>
        // with white-space:pre and forced the long line to overflow.
        const isBlock = Boolean(match) || text.includes('\n');
        if (isBlock) {
          return <CodeBlock code={text} {...(match?.[1] ? { language: match[1] } : {})} />;
        }
        return <InlineCode>{children}</InlineCode>;
      },
      pre(props) {
        // CodeBlock already renders its own <div class="markdown-code"> box, so
        // drop react-markdown's default <pre> wrapper. Keeping it produced an
        // invalid <pre><div> nesting whose white-space:pre broke wrapping and
        // overflowed the page for language-less fenced blocks.
        return <>{props.children}</>;
      },
      a(props) {
        return (
          <a href={props.href} target="_blank" rel="noreferrer noopener">
            {props.children}
          </a>
        );
      },
    }),
    [],
  );

  return (
    <div className="markdown-body">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {content}
      </ReactMarkdown>
    </div>
  );
});
