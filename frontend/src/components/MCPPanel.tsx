import { ChevronDown, Database, FileText, Globe, GitBranch, Loader2, Network, Plus, Save, Search, Shield, Terminal, Trash2, Wrench, Code, Cloud, Settings2 } from 'lucide-react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { t, tOrDefault, translateError } from '../lib/i18n';
import { chatService } from '../services/chatService';
import type { McpServerEntry, McpTemplateEntry, McpToolEntry } from '../types';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from './ui/dropdown-menu';
import { Switch } from './ui/switch';
import { WorkspacePage } from './ui/workspace-page';

// Matches backend mcp.py SECRET_PLACEHOLDER. Carried back on save so stored
// secrets survive an edit without the UI ever seeing the raw value.
const SECRET_PLACEHOLDER = '__CW_SECRET_KEPT__';

type ViewMode = 'list' | 'form' | 'catalog';

// ── Catalog category definition ──────────────────────────────────────────────

interface CatalogCategory {
  value: string;
  label: string;
}

const CATEGORY_VALUES = ['all', 'code', 'data', 'web', 'devops', 'productivity', 'basic'] as const;

// Labels are resolved at render time (not module load) so they follow the
// active language; `CATEGORY_VALUES` keeps the canonical order stable.
function catalogCategories(): CatalogCategory[] {
  return CATEGORY_VALUES.map((value) => ({ value, label: t(`mcp.cat_${value}`) }));
}

function categoryLabel(value: string): string {
  return tOrDefault(`mcp.cat_${value}`, value);
}

// ── SVG icon map for MCP templates (real service logos) ──────────────────────

interface TemplateIconDef {
  svg: string;       // raw SVG markup for a ~24px colored logo
  color: string;
}

const MCP_LOGOS: Record<string, TemplateIconDef> = {
  filesystem: {
    color: '#000000',
    svg: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M22 19a2 2 0 01-2 2H4a2 2 0 01-2-2V5a2 2 0 012-2h5l2 3h9a2 2 0 012 2z"/></svg>',
  },
  git: {
    // GitHub Octocat logo (official brand)
    color: '#1B1F23',
    svg: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.66-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57A12.02 12.02 0 0024 12c0-6.63-5.37-12-12-12z"/></svg>',
  },
  context7: {
    // Upstash Context7 logo (shield icon)
    color: '#7C3AED',
    svg: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>',
  },
  deepwiki: {
    color: '#F59E0B',
    svg: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/></svg>',
  },
  'sequential-thinking': {
    color: '#10B981',
    svg: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/></svg>',
  },
  'memory-server': {
    color: '#3B82F6',
    svg: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 16V8a2 2 0 00-1-1.73l-7-4a2 2 0 00-2 0l-7 4A2 2 0 003 8v8a2 2 0 001 1.73l7 4a2 2 0 002 0l7-4A2 2 0 0021 16z"/><polyline points="3.27 6.96 12 12.01 20.73 6.96"/><line x1="12" y1="22.08" x2="12" y2="12"/></svg>',
  },
  playwright: {
    // Playwright logo (official brand - purple P)
    color: '#EC433F',
    svg: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M5.162 15.835a2.404 2.404 0 0 0 1.747-.743l6.73-6.792a.676.676 0 0 0-.006-.964.693.693 0 0 0-.973.003L5.934 14.13a.686.686 0 0 0-.005.973c.267.272.722.269.99-.005zm4.87-4.923a2.404 2.404 0 0 0 1.747-.743l4.185-4.227a.676.676 0 0 0-.006-.964.693.693 0 0 0-.973.003L10.804 10.1c-.266.269-.264.705.005.973.269.269.723.266.99-.005zM12 2C6.477 2 2 6.477 2 12s4.477 10 10 10 10-4.477 10-10S17.523 2 12 2z"/></svg>',
  },
  'everything-server': {
    color: '#6366F1',
    svg: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M12 1v6m0 6v6m11-7h-6m-6 0H1m15.364-6.364l-4.243 4.243m-3.942 0L4.636 5.636m12.728 12.728l-4.243-4.243m-3.942 0L4.636 18.364"/></svg>',
  },
  // fallback generic icon
  default: {
    color: '#8b949e',
    svg: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M12 1v6m0 6v6m11-7h-6m-6 0H1"/></svg>',
  },
};

function renderLogo(template: McpTemplateEntry): React.ReactNode {
  const def = MCP_LOGOS[template.id] ?? MCP_LOGOS.default;
  return (
    <span
      className="mcp-template-logo"
      style={{ color: def?.color ?? '#8b949e' }}
      dangerouslySetInnerHTML={{ __html: def?.svg ?? '' }}
    />
  );
}

function templateName(template: McpTemplateEntry): string {
  return tOrDefault(`mcp.tpl.${template.id}.name`, template.name);
}

function templateDescription(template: McpTemplateEntry): string {
  return tOrDefault(`mcp.tpl.${template.id}.desc`, template.description);
}

// ── Form state ───────────────────────────────────────────────────────────────

type FormState = {
  id: string | null;
  name: string;
  transport: string;
  command: string;
  args: string;
  cwd: string;
  timeout: string;
  url: string;
  envPairs: { key: string; value: string }[];
  headersPairs: { key: string; value: string }[];
};

const emptyForm = (): FormState => ({
  id: null,
  name: '',
  transport: 'stdio',
  command: '',
  args: '',
  cwd: '',
  timeout: '',
  url: '',
  envPairs: [],
  headersPairs: [],
});

function mapToPairs(values: Record<string, string>): { key: string; value: string }[] {
  return Object.entries(values).map(([key, value]) => ({ key, value }));
}

function pairsToMap(pairs: { key: string; value: string }[]): Record<string, string> {
  return pairs
    .filter((p) => p.key.trim())
    .reduce<Record<string, string>>((acc, p) => {
      acc[p.key.trim()] = p.value;
      return acc;
    }, {});
}

// ── Component ────────────────────────────────────────────────────────────────

interface MCPPanelProps {
  servers: McpServerEntry[];
  templates: McpTemplateEntry[];
  setServers: React.Dispatch<React.SetStateAction<McpServerEntry[]>>;
  onMcpChange?: () => void;
}

function MCPPanel({ servers, templates, setServers, onMcpChange }: MCPPanelProps) {
  const [viewMode, setViewMode] = useState<ViewMode>('list');
  const [form, setForm] = useState<FormState>(emptyForm());
  const [message, setMessage] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  type TestResultType = { ok: boolean; latency_ms: number; tool_count?: number | undefined; error?: string };
  const [testResult, setTestResult] = useState<TestResultType | null>(null);
  const [checking, setChecking] = useState(false);
  const [checkProgress, setCheckProgress] = useState<{ done: number; total: number } | null>(null);
  const [reauthorizing, setReauthorizing] = useState(false);
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null);
  const [search, setSearch] = useState('');

  // Catalog state
  const [catalogSearch, setCatalogSearch] = useState('');
  const [catalogCategory, setCatalogCategory] = useState('all');

  useEffect(() => {
    setMessage(null);
    setTestResult(null);
  }, [viewMode]);

  async function load() {
    try {
      const res = await chatService.listMcps();
      setServers(res.servers);
      onMcpChange?.();
    } catch {
      /* ignore — best-effort */
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function refreshOne(serverId: string) {
    try {
      const res = await chatService.checkMcp(serverId);
      setServers((prev: McpServerEntry[]) => prev.map((s: McpServerEntry) => (s.id === serverId ? { ...s, ...res } : s)));
    } catch {
      /* Best-effort: a failed re-check must not break the save flow. */
    }
  }

  async function handleSave() {
    const canSave =
      !!form.name.trim() &&
      (form.transport === 'stdio' ? !!form.command.trim() : !!form.url.trim());
    if (!canSave) return;
    setSaving(true);
    setMessage(null);
    setTestResult(null);

    const env = pairsToMap(form.envPairs);
    const headers = pairsToMap(form.headersPairs);

    const payload: Record<string, unknown> = {
      name: form.name.trim(),
      transport: form.transport,
    };
    if (form.transport === 'stdio') {
      payload.command = form.command;
      payload.args = form.args;
      payload.cwd = form.cwd;
    } else {
      payload.url = form.url;
    }
    if (form.timeout.trim()) {
      const seconds = Number(form.timeout);
      if (Number.isFinite(seconds) && seconds > 0) payload.timeout = seconds;
    }
    // Always send env/headers (even empty) so omitted keys are cleared, and a
    // SECRET_PLACEHOLDER value preserves the stored secret on the backend.
    payload.env = env;
    payload.headers = headers;

    try {
      const saved = form.id
        ? await chatService.updateMcp(form.id, payload as never)
        : await chatService.createMcp(payload as never);
      await load();
      onMcpChange?.();
      setViewMode('list');
      // Fire a real connection check in the background for accurate status.
      void refreshOne(saved.id);
    } catch (error) {
      setMessage(translateError(error) || t('common.operation_failed'));
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(serverId: string) {
    setSaving(true);
    try {
      await chatService.deleteMcp(serverId);
      await load();
      onMcpChange?.();
      setDeleteConfirm(null);
    } catch (error) {
      setMessage(translateError(error) || t('common.operation_failed'));
    } finally {
      setSaving(false);
    }
  }

  async function handleToggle(server: McpServerEntry) {
    const next = !server.enabled;
    const prevStatus = server.status;
    setServers((prev: McpServerEntry[]) =>
      prev.map((s: McpServerEntry) =>
        s.id === server.id
          ? { ...s, enabled: next, status: next ? (s.status === 'disabled' ? 'unknown' : s.status) : 'disabled' }
          : s,
      ),
    );
    try {
      await chatService.updateMcp(server.id, { enabled: next });
      onMcpChange?.();
      if (next) void refreshOne(server.id);
    } catch (error) {
      setServers((prev: McpServerEntry[]) =>
        prev.map((s: McpServerEntry) => (s.id === server.id ? { ...s, enabled: server.enabled, status: prevStatus } : s)),
      );
      setMessage(translateError(error) || t('common.operation_failed'));
    }
  }

  async function handleToolToggle(server: McpServerEntry, toolName: string, enabled: boolean) {
    const disabled = new Set(server.disabled_tools ?? []);
    if (enabled) disabled.delete(toolName);
    else disabled.add(toolName);
    const disabledTools = Array.from(disabled);
    const optimistic = { ...server, disabled_tools: disabledTools };
    setServers((prev: McpServerEntry[]) => prev.map((s: McpServerEntry) => (s.id === server.id ? optimistic : s)));
    try {
      await chatService.updateMcp(server.id, { disabled_tools: disabledTools });
      onMcpChange?.();
    } catch (error) {
      setServers((prev: McpServerEntry[]) => prev.map((s: McpServerEntry) => (s.id === server.id ? server : s)));
      setMessage(translateError(error) || t('common.operation_failed'));
    }
  }

  async function handleTrustToggle(server: McpServerEntry, trusted: boolean) {
    const optimistic = { ...server, trusted, disabled_tools: trusted ? [] : server.disabled_tools };
    setServers((prev: McpServerEntry[]) => prev.map((s: McpServerEntry) => (s.id === server.id ? optimistic : s)));
    try {
      await chatService.updateMcp(server.id, { trusted, disabled_tools: trusted ? [] : server.disabled_tools });
      onMcpChange?.();
    } catch (error) {
      setServers((prev: McpServerEntry[]) => prev.map((s: McpServerEntry) => (s.id === server.id ? server : s)));
      setMessage(translateError(error) || t('common.operation_failed'));
    }
  }

  async function handleCheckAll() {
    setChecking(true);
    setMessage(null);
    const targets = servers.filter((s) => s.enabled && s.status !== 'disabled');
    if (targets.length === 0) {
      setChecking(false);
      return;
    }
    setCheckProgress({ done: 0, total: targets.length });
    let done = 0;
    try {
      await Promise.all(
        targets.map(async (server) => {
          try {
            const res = await chatService.checkMcp(server.id);
            setServers((prev: McpServerEntry[]) => prev.map((s: McpServerEntry) => (s.id === server.id ? res : s)));
          } catch {
            /* best-effort: keep the stale status */
          } finally {
            done += 1;
            setCheckProgress({ done, total: targets.length });
          }
        }),
      );
      onMcpChange?.();
    } finally {
      setChecking(false);
      setCheckProgress(null);
    }
  }

  async function handleReauthorize(serverId: string) {
    setReauthorizing(true);
    setMessage(null);
    try {
      const server = await chatService.reauthorizeMcp(serverId);
      setServers((prev: McpServerEntry[]) => prev.map((s: McpServerEntry) => (s.id === serverId ? server : s)));
      onMcpChange?.();
      setTestResult(null);
    } catch (error) {
      setMessage(translateError(error) || t('mcp.reauthorize_failed'));
    } finally {
      setReauthorizing(false);
    }
  }

  function relativeTime(iso: string | undefined | null): string {
    if (!iso) return '';
    const then = new Date(iso).getTime();
    if (Number.isNaN(then)) return '';
    const seconds = Math.floor((Date.now() - then) / 1000);
    if (seconds < 60) return t('mcp.last_checked_now');
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60) return t('mcp.last_checked_min').replace('{n}', String(minutes));
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return t('mcp.last_checked_hour').replace('{n}', String(hours));
    return t('mcp.last_checked_day').replace('{n}', String(Math.floor(hours / 24)));
  }

  function formatStatus(status: string): string {
    switch (status) {
      case 'connected': return t('mcp.connected');
      case 'error_connecting': return t('mcp.error_connecting');
      case 'needs_auth': return t('mcp.needs_auth');
      case 'disabled': return t('mcp.disabled');
      default: return t('mcp.unknown');
    }
  }

  function statusDot(status: string): string {
    switch (status) {
      case 'connected': return 'mcp-status-connected';
      case 'error_connecting': return 'mcp-status-error';
      case 'needs_auth': return 'mcp-status-auth';
      case 'disabled': return 'mcp-status-disabled';
      default: return 'mcp-status-unknown';
    }
  }

  function hasStdioFields(f: FormState | McpServerEntry) {
    return f.transport === 'stdio' && !!f.command;
  }

  function isRemoteTransport(f: FormState) {
    return f.transport === 'http' || f.transport === 'sse' || f.transport === 'websocket';
  }

  function startEdit(server: McpServerEntry) {
    setForm({
      id: server.id,
      name: server.name,
      transport: server.transport,
      command: server.command,
      args: server.args,
      cwd: server.cwd,
      timeout: server.timeout != null ? String(server.timeout) : '',
      url: server.url,
      envPairs: mapToPairs(server.env),
      headersPairs: mapToPairs(server.headers),
    });
  }

  function startAdd(template?: McpTemplateEntry) {
    if (template) {
      setForm({
        id: null,
        name: template.name,
        transport: template.transport,
        command: template.command,
        args: template.args,
        cwd: '',
        timeout: '',
        url: template.url,
        envPairs: mapToPairs(template.env ?? {}),
        headersPairs: mapToPairs(template.headers ?? {}),
      });
    } else {
      setForm(emptyForm());
    }
    setViewMode('form');
  }

  function cancelForm() {
    setViewMode('list');
    setForm(emptyForm());
    setMessage(null);
    setTestResult(null);
  }

  async function handleTest() {
    setTesting(true);
    setTestResult(null);
    try {
      const env = pairsToMap(form.envPairs);
      const headers = pairsToMap(form.headersPairs);
      const timeout = form.timeout.trim() ? Number(form.timeout) : undefined;
      const result = await chatService.testMcp({
        name: form.name,
        transport: form.transport,
        command: form.transport === 'stdio' ? form.command : undefined,
        args: form.transport === 'stdio' ? form.args : undefined,
        cwd: form.transport === 'stdio' ? form.cwd : undefined,
        timeout,
        url: isRemoteTransport(form) ? form.url : undefined,
        env,
        headers,
        server_id: form.id ?? undefined,
      } as never);
      setTestResult({
        ok: result.ok,
        latency_ms: result.latency_ms ?? 0,
        tool_count: result.tool_count ?? undefined,
      });
    } catch (error) {
      setTestResult({ ok: false, latency_ms: 0, error: translateError(error) || t('common.operation_failed') });
    } finally {
      setTesting(false);
    }
  }

  function updateEnvPair(index: number, field: 'key' | 'value', value: string) {
    setForm((f: FormState) => {
      const pairs = [...f.envPairs];
      const current = pairs[index];
      if (current) {
        pairs[index] = { ...current, [field]: value };
      }
      return { ...f, envPairs: pairs };
    });
  }

  function removeEnvPair(index: number) {
    setForm((f) => ({ ...f, envPairs: f.envPairs.filter((_, i) => i !== index) }));
  }

  function addEnvPair() {
    setForm((f) => ({ ...f, envPairs: [...f.envPairs, { key: '', value: '' }] }));
  }

  function updateHeaderPair(index: number, field: 'key' | 'value', value: string) {
    setForm((f: FormState) => {
      const pairs = [...f.headersPairs];
      const current = pairs[index];
      if (current) {
        pairs[index] = { ...current, [field]: value };
      }
      return { ...f, headersPairs: pairs };
    });
  }

  function removeHeaderPair(index: number) {
    setForm((f) => ({ ...f, headersPairs: f.headersPairs.filter((_, i) => i !== index) }));
  }

  function addHeaderPair() {
    setForm((f) => ({ ...f, headersPairs: [...f.headersPairs, { key: '', value: '' }] }));
  }

  // ── Filtered servers list ──

  const filteredServers = useMemo(() => {
    let list = servers;
    if (search) {
      const q = search.toLowerCase();
      list = list.filter((s) => s.name.toLowerCase().includes(q) || s.transport.toLowerCase().includes(q));
    }
    return [...list].sort((a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: 'base' }));
  }, [servers, search]);

  // ── Catalog filtered ──

  const catalogFiltered = useMemo(() => {
    let list = templates;
    if (catalogCategory !== 'all') {
      list = list.filter((t) => t.category === catalogCategory);
    }
    if (catalogSearch.trim()) {
      const q = catalogSearch.toLowerCase();
      list = list.filter((t) => t.name.toLowerCase().includes(q) || t.description.toLowerCase().includes(q));
    }
    return list;
  }, [templates, catalogCategory, catalogSearch]);

  const isFormDirty = form.name.trim() && (form.transport === 'stdio' ? !!form.command.trim() : !!form.url.trim());

  // ── LIST VIEW ──

  if (viewMode === 'list') {
    return (
      <WorkspacePage
        eyebrow={t('mcp.title')}
        title={t('mcp.title')}
        description={t('mcp.description')}
        action={
          <Button variant="secondary" onClick={() => setViewMode('catalog')}>
            {t('mcp.more')} ▾
          </Button>
        }
      >
        <div className="workspace-page__content">
          {/* Quick-add card (aligned with provider-quick-card) */}
          {templates.length > 0 && (
            <div className="mcp-quick-card">
              <Network size={18} className="mcp-form-card__icon" />
              <div>
                <strong>{t('mcp.quick_add')}</strong>
                <p>{t('mcp.quick_add_desc')}</p>
                <div className="mcp-template-row">
                  {templates.map((template) => (
                    <button key={template.id} type="button" className="mcp-template-pill" onClick={() => startAdd(template)}>
                      {renderLogo(template)}
                      {templateName(template)}
                    </button>
                  ))}
                  <button type="button" className="mcp-template-pill mcp-template-pill--dashed" onClick={() => startAdd()}>
                    <Plus size={12} />
                    {t('mcp.custom_extension')}
                  </button>
                </div>
              </div>
            </div>
          )}

          {/* List heading: title | search + check | add button */}
          <div className="mcp-list-heading">
            <h2>{t('mcp.my_extensions')} ({servers.length})</h2>
            <div className="mcp-list-actions">
              <div className="mcp-search">
                <Search size={14} className="mcp-search__icon" />
                <input
                  className="mcp-search__input"
                  placeholder={t('mcp.search_placeholder')}
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                />
              </div>
              <Button variant="ghost" onClick={handleCheckAll} disabled={checking} className="mcp-check-all-btn">
                {checking ? <Loader2 size={14} className="animate-spin" /> : <Network size={14} />}
                {checkProgress
                  ? t('mcp.checking_progress').replace('{done}', String(checkProgress.done)).replace('{total}', String(checkProgress.total))
                  : t('mcp.check_connection')}
              </Button>
            </div>
            <Button variant="primary" onClick={() => startAdd()}>
              <Plus size={14} />
              {t('mcp.add_server')}
            </Button>
          </div>

          {/* Server list */}
          {servers.length === 0 ? (
            <div className="mcp-empty">
              <p>{t('mcp.empty')}</p>
              <span>{t('mcp.empty_hint')}</span>
            </div>
          ) : filteredServers.length === 0 ? (
            <div className="mcp-empty">
              <p>{t('mcp.no_match')}</p>
            </div>
          ) : (
            <div className="mcp-list">
              {filteredServers.map((server) => {
                const disabled = !server.enabled || server.status === 'disabled';
                return (
                  <article
                    className={`mcp-card ${disabled ? 'mcp-card--disabled' : ''}`}
                    key={server.id}
                    onClick={() => {
                      startEdit(server);
                      setViewMode('form');
                    }}
                  >
                    {/* Row 1: status dot + name + badge ─ switcher */}
                    <div className="mcp-card__top">
                      <div className="mcp-card__identity">
                        <span className={`mcp-status-dot ${statusDot(server.status)}`} />
                        <strong>{server.name}</strong>
                        <Badge className="mcp-transport-badge">{server.transport}</Badge>
                        {disabled && <Badge>{t('mcp.disabled')}</Badge>}
                      </div>
                      <div className="mcp-card__controls" onClick={(e) => e.stopPropagation()}>
                        <Switch
                          id={`mcp-switch-${server.id}`}
                          checked={server.enabled}
                          onChange={() => handleToggle(server)}
                        />
                      </div>
                    </div>

                    {/* Row 2: subtitle + tool count ─ edit button */}
                    <div className="mcp-card__meta">
                      <span className="mcp-card__subtitle">
                        {(() => {
                          // Normalize: lowercase, strip common suffixes, replace spaces with hyphens
                          const normalize = (s: string) => s.toLowerCase().replace(/\b(mcp|server|extension|tool)\b/g, '').trim().replace(/\s+/g, '-');
                          const normServer = normalize(server.name);
                          const tpl = templates.find(t => normalize(t.id) === normServer || normalize(t.name) === normServer);
                          return tpl ? templateDescription(tpl) : server.name;
                        })()} · {t('mcp.tool_count').replace('{count}', String(server.tool_count || 0))}
                        {server.error_message && <span className="mcp-error-msg"> · {server.error_message}</span>}
                        {relativeTime(server.last_checked_at) && <span className="mcp-last-checked"> · {relativeTime(server.last_checked_at)}</span>}
                      </span>
                      <button
                        className="mcp-card-edit-btn"
                        onClick={(e) => {
                          e.stopPropagation();
                          startEdit(server);
                          setViewMode('form');
                        }}
                      >
                        {t('mcp.edit')}
                      </button>
                    </div>
                  </article>
                );
              })}
            </div>
          )}

          {message && <p className="mcp-message">{message}</p>}
        </div>
      </WorkspacePage>
    );
  }

  // ========== CATALOG VIEW (service directory, "更多" entry) ==========

  if (viewMode === 'catalog') {
    return (
      <WorkspacePage
        eyebrow={t('mcp.title')}
        title={t('mcp.catalog_title')}
        description={t('mcp.catalog_desc')}
        action={
          <Button variant="ghost" onClick={() => setViewMode('list')}>
            {t('mcp.back')}
          </Button>
        }
      >
        <div className="workspace-page__content">
          {/* Search + categories */}
          <div style={{ marginBottom: 16 }}>
            <input
              type="text"
              className="mcp-catalog-search"
              value={catalogSearch}
              onChange={(event) => setCatalogSearch(event.target.value)}
              placeholder={t('mcp.catalog_search')}
            />
          </div>

          {/* Category tabs */}
          <div className="mcp-catalog-categories">
            {catalogCategories().map((cat) => (
              <button
                key={cat.value}
                className={`mcp-catalog-tab ${catalogCategory === cat.value ? 'mcp-catalog-tab--active' : ''}`}
                onClick={() => setCatalogCategory(cat.value)}
              >
                {cat.label}
              </button>
            ))}
          </div>

          {/* Service card grid */}
          <div className="mcp-catalog-grid">
            {catalogFiltered.map((template) => (
              <div
                className="mcp-catalog-card"
                key={template.id}
                onClick={() => startAdd(template)}
              >
                <div className="mcp-catalog-card__icon" style={{ color: (MCP_LOGOS[template.id] ?? MCP_LOGOS.default)?.color ?? '#8b949e' }}>
                  {renderLogo(template)}
                </div>
                <div className="mcp-catalog-card__name">{templateName(template)}</div>
                <div className="mcp-catalog-card__desc">{templateDescription(template)}</div>
                <div className="mcp-catalog-card__tags">
                  <Badge className="mcp-transport-badge">{template.transport}</Badge>
                  {template.category && <Badge className="mcp-category-badge">{categoryLabel(template.category)}</Badge>}
                </div>
              </div>
            ))}
          </div>
        </div>
      </WorkspacePage>
    );
  }

  // ========== FORM VIEW (server detail/edit form, aligned with provider form) ==========

  const editingServer = form.id ? servers.find((s) => s.id === form.id) : undefined;
  const disabledTools = new Set(editingServer?.disabled_tools ?? []);

  return (
    <WorkspacePage
      className="mcp-shell--form"
      eyebrow={t('mcp.title')}
      title={form.id ? servers.find((s) => s.id === form.id)?.name || '' : t('mcp.add_title')}
      action={
        <Button variant="ghost" onClick={cancelForm}>
          {t('mcp.back')}
        </Button>
      }
    >
      <div className="mcp-form-card">
        <Network size={18} className="mcp-form-card__icon" />
        <div className="mcp-form-card__body">
          {/* Connection status row (only on edit) */}
          {form.id && editingServer && (
            <div className="mcp-status-row">
              <span className={`mcp-status-dot ${statusDot(editingServer.status || 'unknown')}`} style={{ width: 12, height: 12 }} />
              <Badge className={editingServer.status === 'connected' ? 'mcp-status-badge--ok' : ''}>
                {formatStatus(editingServer.status || 'unknown')}
              </Badge>
              <Button
                variant="secondary"
                onClick={handleTest}
                disabled={testing || (hasStdioFields({ ...emptyForm(), ...form }) && !form.command.trim()) || (isRemoteTransport(form) && !form.url.trim())}
                className="mcp-test-btn"
              >
                {testing ? (
                  <Loader2 size={14} className="animate-spin" />
                ) : (
                  <Network size={14} />
                )}
                {t('mcp.test_connection')}
              </Button>
              {testResult && (
                <span className={`mcp-test-result ${testResult.ok ? 'mcp-test-result--ok' : 'mcp-test-result--error'}`}>
                  {testResult.ok
                    ? t('mcp.test_success').replace('{latency_ms}', String(testResult.latency_ms)).replace('{tool_count}', String(testResult.tool_count || 0))
                    : `${t('mcp.test_failed')}: ${testResult.error || ''}`}
                </span>
              )}
              {editingServer?.status === 'needs_auth' && (
                <Button variant="secondary" onClick={() => void handleReauthorize(form.id!)} disabled={reauthorizing} className="mcp-reauth-btn">
                  {reauthorizing ? <Loader2 size={14} className="animate-spin" /> : <Shield size={14} />}
                  {t('mcp.reauthorize')}
                </Button>
              )}
            </div>
          )}

          {/* ── Connection config ── */}
          <div className="mcp-detail-section">{t('mcp.connection_config')}</div>

          <label className="field">
            <span>{t('mcp.name')}</span>
            <input
              value={form.name}
              onChange={(event) => setForm({ ...form, name: event.target.value })}
              placeholder={t('mcp.name_placeholder')}
              disabled={saving}
            />
          </label>

          <div className="field">
            <span>{t('mcp.transport')}</span>
            <div className="mcp-transport-row" role="group" aria-label={t('mcp.transport')}>
              <button
                type="button"
                className={`mcp-transport-pill ${form.transport === 'stdio' ? 'mcp-transport-pill--active' : ''}`}
                aria-pressed={form.transport === 'stdio'}
                onClick={() => form.transport !== 'stdio' && setForm({ ...form, transport: 'stdio', command: '', args: '' })}
                disabled={saving}
              >
                {t('mcp.transport_stdio')}
              </button>
              <button
                type="button"
                className={`mcp-transport-pill ${form.transport === 'http' ? 'mcp-transport-pill--active' : ''}`}
                aria-pressed={form.transport === 'http'}
                onClick={() => form.transport !== 'http' && setForm({ ...form, transport: 'http', url: '' })}
                disabled={saving}
              >
                {t('mcp.transport_http')}
              </button>
              <button
                type="button"
                className={`mcp-transport-pill ${form.transport === 'sse' ? 'mcp-transport-pill--active' : ''}`}
                aria-pressed={form.transport === 'sse'}
                onClick={() => form.transport !== 'sse' && setForm({ ...form, transport: 'sse', url: '' })}
                disabled={saving}
              >
                {t('mcp.transport_sse')}
              </button>
              <button
                type="button"
                className={`mcp-transport-pill ${form.transport === 'websocket' ? 'mcp-transport-pill--active' : ''}`}
                aria-pressed={form.transport === 'websocket'}
                onClick={() => form.transport !== 'websocket' && setForm({ ...form, transport: 'websocket', url: '' })}
                disabled={saving}
              >
                {t('mcp.transport_websocket')}
              </button>
            </div>
          </div>

          {form.transport === 'stdio' && (
            <div className="mcp-cmd-row">
              <label className="field">
                <span>{t('mcp.command')}</span>
                <input
                  value={form.command}
                  onChange={(event) => setForm({ ...form, command: event.target.value })}
                  placeholder={t('mcp.command_placeholder')}
                  disabled={saving}
                />
              </label>
              <label className="field mcp-args-field">
                <span className="mcp-args-label">{t('mcp.args')}</span>
                <input
                  value={form.args}
                  onChange={(event) => setForm({ ...form, args: event.target.value })}
                  placeholder={t('mcp.args_placeholder')}
                  disabled={saving}
                />
              </label>
              <label className="field mcp-cwd-field">
                <span className="mcp-args-label">{t('mcp.cwd')}</span>
                <input
                  value={form.cwd}
                  onChange={(event) => setForm({ ...form, cwd: event.target.value })}
                  placeholder={t('mcp.cwd_placeholder')}
                  disabled={saving}
                />
              </label>
            </div>
          )}

          {(form.transport === 'http' || form.transport === 'sse' || form.transport === 'websocket') && (
            <label className="field">
              <span>{t('mcp.url')}</span>
              <input
                value={form.url}
                onChange={(event) => setForm({ ...form, url: event.target.value })}
                placeholder={t('mcp.url_placeholder')}
                disabled={saving}
              />
            </label>
          )}

          {/* ── Divider ── */}
          <div className="mcp-divider"></div>

          {/* ── Discovered tools (server detail only) ── */}
          {form.id && editingServer?.tools && (
            <div className="mcp-detail-section">{t('mcp.discovered_tools').replace('{count}', String(editingServer.tools.length))}</div>
          )}

          {editingServer?.tools?.map((tool: McpToolEntry) => {
            const enabled = !disabledTools.has(tool.name);
            return (
              <div className={`mcp-tool-row ${enabled ? '' : 'mcp-tool-row--disabled'}`} key={tool.name}>
                <div>
                  <div className="mcp-tool-name">{tool.name}</div>
                  {tool.description && <div className="mcp-tool-desc">{tool.description}</div>}
                </div>
                <Switch id={`mcp-tool-${tool.name}`} checked={enabled} onChange={() => void handleToolToggle(editingServer, tool.name, !enabled)} />
              </div>
            );
          })}

          {/* ── Divider ── */}
          <div className="mcp-divider"></div>

          {/* ── Advanced settings ── */}
          <div className="mcp-detail-section">{t('mcp.advanced_settings')}</div>

          {/* Auto-approve all */}
          <div className="mcp-tool-row">
            <div>
              <div className="mcp-tool-name">{t('mcp.trust_server')}</div>
              <div className="mcp-tool-desc">{t('mcp.trust_desc')}</div>
            </div>
            <Switch id="mcp-trust" checked={Boolean(editingServer?.trusted)} onChange={() => editingServer && void handleTrustToggle(editingServer, !editingServer.trusted)} />
          </div>

          {/* Connection timeout */}
          <label className="field">
            <span>{t('mcp.timeout')}</span>
            <input
              value={form.timeout}
              onChange={(event) => setForm({ ...form, timeout: event.target.value })}
              placeholder={t('mcp.timeout_placeholder')}
              type="number"
              min="1"
              step="1"
              disabled={saving}
            />
          </label>

          {/* Environment variables */}
          <div className="mcp-tool-row">
            <div>
              <div className="mcp-tool-name">{t('mcp.env_vars')}</div>
            </div>
            <Button variant="secondary" onClick={() => addEnvPair()} className="mcp-edit-env-btn">
              {t('mcp.edit')}
            </Button>
          </div>

          {/* ── Environment variables editor ── */}
          <div className="field">
            {form.envPairs.map((pair, i) => {
              const isSecret = pair.value === SECRET_PLACEHOLDER;
              return (
                <div className="mcp-env-row" key={i}>
                  <input
                    value={pair.key}
                    onChange={(event) => updateEnvPair(i, 'key', event.target.value)}
                    placeholder={t('mcp.env_key')}
                    className="mcp-env-input"
                    disabled={saving}
                  />
                  <input
                    value={pair.value}
                    onChange={(event) => updateEnvPair(i, 'value', event.target.value)}
                    placeholder={t('mcp.env_value')}
                    className="mcp-env-input"
                    type={isSecret ? 'password' : 'text'}
                    disabled={saving}
                  />
                  {isSecret && <small className="mcp-secret-hint">{t('mcp.secret_kept')}</small>}
                  <Button variant="icon" className="mcp-env-remove" onClick={() => removeEnvPair(i)} disabled={saving} title={t('common.delete')}>
                    <Trash2 size={12} />
                  </Button>
                </div>
              );
            })}
            <Button variant="ghost" onClick={addEnvPair} disabled={saving} className="mcp-env-add-btn">
              <Plus size={12} />
              {t('mcp.env_add')}
            </Button>
          </div>

          {/* HTTP headers */}
          {isRemoteTransport(form) && (
            <>
              <div className="mcp-tool-row">
                <div>
                  <div className="mcp-tool-name">{t('mcp.headers')}</div>
                  <div className="mcp-tool-desc">{t('mcp.headers_desc')}</div>
                </div>
                <Button variant="secondary" onClick={() => addHeaderPair()} className="mcp-edit-env-btn">
                  {t('mcp.edit')}
                </Button>
              </div>
              <div className="field">
                {form.headersPairs.map((pair, i) => {
                  const isSecret = pair.value === SECRET_PLACEHOLDER;
                  return (
                    <div className="mcp-env-row" key={i}>
                      <input
                        value={pair.key}
                        onChange={(event) => updateHeaderPair(i, 'key', event.target.value)}
                        placeholder={t('mcp.headers_key')}
                        className="mcp-env-input"
                        disabled={saving}
                      />
                      <input
                        value={pair.value}
                        onChange={(event) => updateHeaderPair(i, 'value', event.target.value)}
                        placeholder={t('mcp.headers_value')}
                        className="mcp-env-input"
                        type={isSecret ? 'password' : 'text'}
                        disabled={saving}
                      />
                      {isSecret && <small className="mcp-secret-hint">{t('mcp.secret_kept')}</small>}
                      <Button variant="icon" className="mcp-env-remove" onClick={() => removeHeaderPair(i)} disabled={saving} title={t('common.delete')}>
                        <Trash2 size={12} />
                      </Button>
                    </div>
                  );
                })}
                <Button variant="ghost" onClick={addHeaderPair} disabled={saving} className="mcp-env-add-btn">
                  <Plus size={12} />
                  {t('mcp.headers_add')}
                </Button>
              </div>
            </>
          )}

          {/* ── Footer ── */}
          <div className="mcp-form-footer">
            {form.id && (
              <Button variant="ghost" className="mcp-danger-button" onClick={() => setDeleteConfirm(form.id)} disabled={saving}>
                <Trash2 size={14} />
                {t('common.delete')}
              </Button>
            )}

            <div className="mcp-form-footer__actions">
              <Button variant="ghost" onClick={cancelForm} disabled={saving}>
                {t('mcp.cancel')}
              </Button>
              <Button variant="primary" onClick={handleSave} disabled={saving || !isFormDirty}>
                {saving ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />}
                {t('mcp.save')}
              </Button>
            </div>
          </div>

          {message && <p className="mcp-message">{message}</p>}

          {/* Delete inline confirm */}
          {deleteConfirm && form.id && (
            <div className="mcp-delete-confirm">
              <p>{t('mcp.delete_confirm')}</p>
              <Button variant="icon" className="mcp-delete-confirm__button" onClick={() => handleDelete(form.id!)} disabled={saving}>
                {t('common.delete')}
              </Button>
            </div>
          )}
        </div>
      </div>
    </WorkspacePage>
  );
}

export { MCPPanel };
