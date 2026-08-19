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
  | 'xml'
  | 'java'
  | 'go'
  | 'rust'
  | 'c'
  | 'cpp'
  | 'csharp'
  | 'sql'
  | 'php'
  | 'ruby'
  | 'kotlin'
  | 'swift'
  | 'powershell'
  | 'dockerfile'
  | 'toml'
  | 'ini'
  | 'graphql'
  | 'jsonc'
  | 'vue'
  | 'svelte'
  | 'makefile'
  | 'cmake'
  | 'scala'
  | 'dart'
  | 'lua'
  | 'r'
  | 'haskell'
  | 'fish'
  | 'groovy'
  | 'objective-c'
  | 'nginx'
  | 'mermaid'
  | 'latex'
  | 'protobuf'
  | 'solidity'
  | 'zig';

/** Canonical language ids (also includes aliases via LANGUAGE_ALIASES). */
const SUPPORTED_LANGS: readonly SupportedLanguage[] = [
  'text',
  'javascript',
  'typescript',
  'tsx',
  'jsx',
  'python',
  'bash',
  'json',
  'markdown',
  'css',
  'html',
  'diff',
  'yaml',
  'xml',
  'java',
  'go',
  'rust',
  'c',
  'cpp',
  'csharp',
  'sql',
  'php',
  'ruby',
  'kotlin',
  'swift',
  'powershell',
  'dockerfile',
  'toml',
  'ini',
  'graphql',
  'jsonc',
  'vue',
  'svelte',
  'makefile',
  'cmake',
  'scala',
  'dart',
  'lua',
  'r',
  'haskell',
  'fish',
  'groovy',
  'objective-c',
  'nginx',
  'mermaid',
  'latex',
  'protobuf',
  'solidity',
  'zig',
];

/** Common fence aliases -> canonical shiki language id. */
const LANGUAGE_ALIASES: Record<string, SupportedLanguage> = {
  js: 'javascript',
  ts: 'typescript',
  py: 'python',
  sh: 'bash',
  shell: 'bash',
  zsh: 'bash',
  md: 'markdown',
  mdx: 'markdown',
  yml: 'yaml',
  golang: 'go',
  rs: 'rust',
  'c++': 'cpp',
  cc: 'cpp',
  csharp: 'csharp',
  cs: 'csharp',
  'c#': 'csharp',
  rb: 'ruby',
  kt: 'kotlin',
  pwsh: 'powershell',
  ps1: 'powershell',
  docker: 'dockerfile',
  cfg: 'ini',
  properties: 'ini',
  gql: 'graphql',
  json5: 'jsonc',
  make: 'makefile',
  mk: 'makefile',
  hs: 'haskell',
  objc: 'objective-c',
  m: 'objective-c',
  nginxconf: 'nginx',
  tex: 'latex',
  proto: 'protobuf',
  sol: 'solidity',
};

const PLAIN_ALIASES: ReadonlySet<string> = new Set(['text', 'plain', 'txt', 'plaintext']);

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
        java: () => import('shiki/langs/java'),
        go: () => import('shiki/langs/go'),
        rust: () => import('shiki/langs/rust'),
        c: () => import('shiki/langs/c'),
        cpp: () => import('shiki/langs/cpp'),
        csharp: () => import('shiki/langs/csharp'),
        sql: () => import('shiki/langs/sql'),
        php: () => import('shiki/langs/php'),
        ruby: () => import('shiki/langs/ruby'),
        kotlin: () => import('shiki/langs/kotlin'),
        swift: () => import('shiki/langs/swift'),
        powershell: () => import('shiki/langs/powershell'),
        dockerfile: () => import('shiki/langs/dockerfile'),
        toml: () => import('shiki/langs/toml'),
        ini: () => import('shiki/langs/ini'),
        graphql: () => import('shiki/langs/graphql'),
        jsonc: () => import('shiki/langs/jsonc'),
        vue: () => import('shiki/langs/vue'),
        svelte: () => import('shiki/langs/svelte'),
        makefile: () => import('shiki/langs/makefile'),
        cmake: () => import('shiki/langs/cmake'),
        scala: () => import('shiki/langs/scala'),
        dart: () => import('shiki/langs/dart'),
        lua: () => import('shiki/langs/lua'),
        r: () => import('shiki/langs/r'),
        haskell: () => import('shiki/langs/haskell'),
        fish: () => import('shiki/langs/fish'),
        groovy: () => import('shiki/langs/groovy'),
        'objective-c': () => import('shiki/langs/objective-c'),
        nginx: () => import('shiki/langs/nginx'),
        mermaid: () => import('shiki/langs/mermaid'),
        latex: () => import('shiki/langs/latex'),
        protobuf: () => import('shiki/langs/protobuf'),
        solidity: () => import('shiki/langs/solidity'),
        zig: () => import('shiki/langs/zig'),
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
  if (PLAIN_ALIASES.has(normalized)) return 'text';
  const aliased = LANGUAGE_ALIASES[normalized];
  if (aliased) return aliased;
  if ((SUPPORTED_LANGS as readonly string[]).includes(normalized)) return normalized as SupportedLanguage;
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
