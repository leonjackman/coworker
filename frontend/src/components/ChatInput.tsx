import {
  Check,
  ChevronDown,
  Folder,
  FolderPlus,
  GitBranch,
  ListChecks,
  Paperclip,
  Pencil,
  Send,
  ShieldCheck,
  Square,
  X,
} from "lucide-react";
import { useEffect, useRef, useState, type ClipboardEvent } from "react";
import { t } from "../lib/i18n";
import type { Autonomy, ComposerAttachment, SessionReference, WorkMode } from "../types";
import { Button } from "./ui/button";
import { CardSlot } from "./ui/card-slot";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "./ui/dropdown-menu";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "./ui/select";
import { Textarea } from "./ui/textarea";
import { Tooltip } from "./ui/tooltip";

export interface ModelOption {
  id: string;
  label: string;
  provider?: string;
}

export interface WorkspaceOption {
  id: string;
  name: string;
  path: string;
}

interface ChatInputProps {
  value: string;
  disabled: boolean;
  isThinking: boolean;
  workMode: WorkMode;
  autonomy: Autonomy;
  selectedModel: string;
  attachments: ComposerAttachment[];
  references: SessionReference[];
  modelOptions: ModelOption[];
  onChange: (value: string) => void;
  onSend: () => void;
  onStop: () => void;
  onWorkModeChange: (value: WorkMode) => void;
  onAutonomyChange: (value: Autonomy) => void;
  onModelChange: (value: string) => void;
  onAttachmentsChange: (attachments: ComposerAttachment[]) => void;
  onReferencesChange: (references: SessionReference[]) => void;
  onResolveSession?: (sessionId: string) => Promise<SessionReference | null>;
  editing?: boolean;
  onCancelEdit?: () => void;
  branchStatus?: { isRepo: boolean; branch: string | null } | null;
  workspaceLabel?: string;
  /** 新对话草稿态：在 composer 顶部显示 workspace 选择器 */
  showWorkspacePicker?: boolean;
  workspaceOptions?: WorkspaceOption[];
  activeWorkspaceId?: string;
  onSelectWorkspace?: (projectId: string) => void;
  onCreateWorkspace?: () => void;
}

const SLASH_COMMANDS = ["/help", "/new", "/clear", "/goal", "/providers", "/settings"];
const MAX_ATTACHMENT_CHARS = 120_000;

const SESSION_ID_RE = /\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b/gi;

export function extractSessionIds(text: string): string[] {
  return Array.from(text.matchAll(SESSION_ID_RE), (match) => match[0].toLowerCase());
}

function isTextAttachment(file: File) {
  if (file.type.startsWith("text/")) return true;
  return /\.(c|cc|cpp|cs|css|csv|go|h|hpp|html|java|js|json|jsx|kt|log|md|mdx|php|py|rb|rs|sh|sql|swift|toml|ts|tsx|txt|vue|xml|yaml|yml)$/i.test(
    file.name,
  );
}

async function buildAttachment(file: File): Promise<ComposerAttachment> {
  const base = {
    id: `${file.name}-${file.size}-${Date.now()}-${Math.random().toString(16).slice(2)}`,
    name: file.name,
    size: file.size,
    type: file.type || "file",
  };
  if (!isTextAttachment(file)) {
    return { ...base, binary: true };
  }
  try {
    const content = await file.text();
    const truncated = content.length > MAX_ATTACHMENT_CHARS;
    return {
      ...base,
      content: truncated ? content.slice(0, MAX_ATTACHMENT_CHARS) : content,
      truncated,
      binary: false,
    };
  } catch (error) {
    return {
      ...base,
      binary: true,
      error:
        error instanceof Error ? error.message : "Unable to read attachment",
    };
  }
}

export function ChatInput({
  value,
  disabled,
  isThinking,
  workMode,
  autonomy,
  selectedModel,
  attachments,
  references,
  modelOptions,
  onChange,
  onSend,
  onStop,
  onWorkModeChange,
  onAutonomyChange,
  onModelChange,
  onAttachmentsChange,
  onReferencesChange,
  onResolveSession,
  editing = false,
  onCancelEdit,
  branchStatus = null,
  workspaceLabel,
  showWorkspacePicker = false,
  workspaceOptions = [],
  activeWorkspaceId,
  onSelectWorkspace,
  onCreateWorkspace,
}: ChatInputProps) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const mirrorRef = useRef<HTMLDivElement>(null);
  const [showCommands, setShowCommands] = useState(false);
  const [commandIndex, setCommandIndex] = useState(0);
  const [highlights, setHighlights] = useState<{ left: number; top: number; width: number; height: number }[]>([]);
  const activeWorkspace = workspaceOptions.find(
    (option) => option.id === activeWorkspaceId,
  );
  const workspaceMissing = showWorkspacePicker && !activeWorkspace;
  const canSend =
    Boolean(value.trim() || attachments.length > 0) && !workspaceMissing;

  useEffect(() => {
    const nonWs = value.search(/\S/);
    const startsSlash = nonWs === 0 && value.charAt(0) === "/";
    const firstToken = startsSlash ? value.slice(1).split(/\s/)[0] ?? "" : "";
    const commandCommitted = startsSlash && value.length > firstToken.length + 1;
    // Pop only while a NEW leading command is being typed (starts with "/" and
    // no whitespace after it yet). A committed command (followed by whitespace)
    // closes the menu; mid-string "/" never pops (industry: commands at start).
    if (commandCommitted || !startsSlash) {
      setShowCommands(false);
    } else {
      setShowCommands(true);
      setCommandIndex(0);
    }
  }, [value]);

  // Leading slash token (text after the leading "/", up to whitespace) for
  // filtering — only the leading command is a real command. When the partial
  // matches nothing (e.g. "/" inserted at the head of existing text), fall back
  // to showing ALL commands so the card still pops.
  const nonWs = value.search(/\S/);
  const commandQuery = nonWs === 0 && value.charAt(0) === "/" ? value.slice(1).split(/\s/)[0] ?? "" : "";
  const filteredCommands = SLASH_COMMANDS.filter((command) => command.slice(1).startsWith(commandQuery));
  const displayedCommands = filteredCommands.length > 0 ? filteredCommands : SLASH_COMMANDS;
  const activeCommandIndex = Math.min(commandIndex, Math.max(0, displayedCommands.length - 1));

  useEffect(() => {
    if (!showCommands) return;
    const active = menuRef.current?.querySelector(".slash-menu__item--active");
    active?.scrollIntoView({ block: "nearest" });
  }, [activeCommandIndex, showCommands]);

  /** The LEADING command token only (a known command or a prefix being typed).
   * Only the leading token executes (industry: commands run at message start),
   * so only it gets the highlight box — mid-string "/goal" is plain text. */
  function commandTokenRanges(text: string): { start: number; end: number }[] {
    const startIndex = text.search(/\S/); // first non-whitespace char
    if (startIndex === -1) return [];
    const match = /\/[\w-]+/.exec(text.slice(startIndex));
    if (!match || match.index !== 0) return []; // slash token must be the FIRST token
    const token = match[0];
    const name = token.slice(1);
    if (!SLASH_COMMANDS.some((command) => command.slice(1).startsWith(name))) return [];
    return [{ start: startIndex + match.index, end: startIndex + match.index + token.length }];
  }

  /** Measure command tokens and position rounded-highlight boxes over them.
   * The mirror renders plain text (identical metrics to the textarea) so the
   * caret stays aligned; the pill is a non-layout overlay. */
  function updateHighlights() {
    const mirror = mirrorRef.current;
    const node = mirror?.firstChild;
    if (!mirror || !node || !value) {
      setHighlights([]);
      return;
    }
    const mirrorRect = mirror.getBoundingClientRect();
    const boxes = commandTokenRanges(value)
      .map((range) => {
        try {
          const domRange = document.createRange();
          domRange.setStart(node, range.start);
          domRange.setEnd(node, range.end);
          const rect = domRange.getBoundingClientRect();
          return {
            left: rect.left - mirrorRect.left - 3,
            top: rect.top - mirrorRect.top - 1,
            width: rect.width + 6,
            height: rect.height + 2,
          };
        } catch {
          return null;
        }
      })
      .filter((box): box is { left: number; top: number; width: number; height: number } => box !== null);
    setHighlights(boxes);
  }

  useEffect(() => {
    updateHighlights();
  }, [value]);

  async function addFiles(files: FileList | null) {
    if (!files) return;
    const nextAttachments = await Promise.all(
      Array.from(files).map((file) => buildAttachment(file)),
    );
    onAttachmentsChange([...attachments, ...nextAttachments]);
    if (fileInputRef.current) fileInputRef.current.value = "";
  }

  function removeAttachment(id: string) {
    onAttachmentsChange(
      attachments.filter((attachment) => attachment.id !== id),
    );
  }

  async function addReferenceFromText(text: string) {
    if (!onResolveSession) return;
    const candidateIds = [...new Set(extractSessionIds(text))];
    const existing = new Set(references.map((reference) => reference.id));
    const pending = candidateIds.filter((id) => !existing.has(id));
    if (pending.length === 0) return;
    const resolved = (await Promise.all(pending.map((id) => onResolveSession(id)))).filter(
      (reference): reference is SessionReference => reference !== null,
    );
    if (resolved.length === 0) return;
    const merged = [...references];
    const mergedIds = new Set(merged.map((reference) => reference.id));
    for (const reference of resolved) {
      if (!mergedIds.has(reference.id)) {
        merged.push(reference);
        mergedIds.add(reference.id);
      }
    }
    onReferencesChange(merged);
  }

  async function handlePaste(event: ClipboardEvent<HTMLTextAreaElement>) {
    const pasted = event.clipboardData.getData("text");
    if (pasted && extractSessionIds(pasted).length > 0) {
      void addReferenceFromText(pasted);
    }
  }

  function removeReference(id: string) {
    onReferencesChange(references.filter((reference) => reference.id !== id));
  }

  function insertCommand(command: string) {
    const current = value;
    const selStart = textareaRef.current?.selectionStart ?? current.length;
    // Replace the slash token the cursor is currently inside (scan back from the
    // cursor to the preceding "/", stopping at whitespace), so "hello /" + select
    // /help produces "hello /help " instead of "hello //help ".
    let tokenStart = -1;
    for (let i = selStart - 1; i >= 0; i -= 1) {
      if (current.charAt(i) === "/") {
        tokenStart = i;
        break;
      }
      if (/\s/.test(current.charAt(i))) break;
    }
    const next =
      tokenStart >= 0
        ? current.slice(0, tokenStart) + `${command} ` + current.slice(selStart)
        : current.slice(0, selStart) + `${command} ` + current.slice(selStart);
    onChange(next);
    setShowCommands(false);
    requestAnimationFrame(() => {
      textareaRef.current?.focus();
      const pos = tokenStart >= 0 ? tokenStart + command.length + 1 : selStart + command.length + 1;
      try {
        textareaRef.current?.setSelectionRange(pos, pos);
      } catch {
        // ignore
      }
    });
  }

  /** True when the caret sits on/right after the LEADING command token.
   * Only the leading command is a real command (industry), so only it gets
   * whole-token backspace deletion. */
  function commandTokenAt(cursor: number): { start: number; end: number } | null {
    const current = value;
    const nonWs = current.search(/\S/);
    if (nonWs !== 0 || current.charAt(0) !== "/") return null;
    // The FULL leading token (from "/" to the next whitespace/end).
    let end = 1;
    while (end < current.length && !/\s/.test(current.charAt(end))) end += 1;
    const name = current.slice(1, end);
    if (!SLASH_COMMANDS.some((command) => command.slice(1).startsWith(name))) return null;
    if (current.charAt(end) === " ") end += 1;
    // Only whole-delete when the caret is on/at the token (not past it).
    if (cursor > end) return null;
    return { start: 0, end };
  }

  function deleteCommandToken(cursor: number): void {
    const token = commandTokenAt(cursor);
    if (!token) return;
    const next = value.slice(0, token.start) + value.slice(token.end);
    onChange(next);
    requestAnimationFrame(() => {
      textareaRef.current?.focus();
      try {
        textareaRef.current?.setSelectionRange(token.start, token.start);
      } catch {
        // ignore
      }
    });
  }

  const nextAutonomy: Autonomy = autonomy === "supervised" ? "guarded" : autonomy === "guarded" ? "autonomous" : "supervised";

  return (
    <footer className="composer">
      <CardSlot className="composer__card">
        {editing && (
          <div className="composer__edit-bar">
            <span className="composer__edit-label">
              <Pencil size={13} />
              {t("message.edit")}
            </span>
            <Button
              variant="ghost"
              size="xs"
              onClick={onCancelEdit}
              aria-label={t("message.edit_cancel")}
            >
              <X size={13} />
              {t("message.edit_cancel")}
            </Button>
          </div>
        )}
        {showWorkspacePicker && (
          <div className="composer__context">
            <DropdownMenu>
              <DropdownMenuTrigger
                className={`composer__ws-chip ${workspaceMissing ? "composer__ws-chip--empty" : ""}`}
                aria-label={t("chat.workspace_pick")}
                title={activeWorkspace?.path ?? t("chat.workspace_pick")}
              >
                <span className="composer__ws-icon">
                  <Folder size={11} />
                </span>
                <span className="composer__ws-value">
                  {activeWorkspace ? activeWorkspace.name : t("chat.workspace_pick")}
                </span>
                <ChevronDown size={13} className="composer__ws-chevron" />
              </DropdownMenuTrigger>
              <DropdownMenuContent align="start" className="composer__ws-menu">
                {workspaceOptions.length > 0 && (
                  <DropdownMenuLabel>{t("chat.workspace_switch")}</DropdownMenuLabel>
                )}
                {workspaceOptions.map((option) => (
                  <DropdownMenuItem
                    key={option.id}
                    className="composer__ws-item"
                    onClick={() => onSelectWorkspace?.(option.id)}
                  >
                    <Folder size={14} />
                    <span className="composer__ws-item-text">
                      <span className="composer__ws-item-name">{option.name}</span>
                      <span className="composer__ws-item-path">{option.path}</span>
                    </span>
                    {option.id === activeWorkspaceId && (
                      <Check size={14} className="composer__ws-item-check" />
                    )}
                  </DropdownMenuItem>
                ))}
                {workspaceOptions.length > 0 && <DropdownMenuSeparator />}
                <DropdownMenuItem onClick={() => onCreateWorkspace?.()}>
                  <FolderPlus size={14} />
                  {t("chat.workspace_new")}
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
            {activeWorkspace && (
              <span className="composer__ws-path" title={activeWorkspace.path}>
                {activeWorkspace.path}
              </span>
            )}
          </div>
        )}

        <div className="composer__input-box">
          <div className="composer__editor">
            <div className="composer__input-mirror" ref={mirrorRef} aria-hidden="true">
              {value}
              {highlights.map((box, index) => (
                <span
                  key={index}
                  className="composer__cmd-highlight"
                  style={{ left: box.left, top: box.top, width: box.width, height: box.height }}
                />
              ))}
            </div>
            <Textarea
              ref={textareaRef}
              className="composer__input"
              value={value}
              onChange={(event) => onChange(event.target.value)}
              onPaste={(event) => void handlePaste(event)}
              onScroll={(event) => {
                const mirror = mirrorRef.current;
                if (mirror) mirror.scrollTop = event.currentTarget.scrollTop;
                updateHighlights();
              }}
              onKeyDown={(event) => {
                if (showCommands && displayedCommands.length > 0) {
                  if (event.key === "ArrowDown") {
                    event.preventDefault();
                    setCommandIndex((index) => (index + 1) % displayedCommands.length);
                    return;
                  }
                  if (event.key === "ArrowUp") {
                    event.preventDefault();
                    setCommandIndex((index) => (index - 1 + displayedCommands.length) % displayedCommands.length);
                    return;
                  }
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    insertCommand(displayedCommands[activeCommandIndex] ?? displayedCommands[0] ?? "");
                    return;
                  }
                }
                if (event.key === "Backspace") {
                  const el = textareaRef.current;
                  // A real selection (e.g. Cmd+A) must be deleted as a whole by the
                  // default behavior — only whole-delete the leading command when
                  // the caret sits on it (no selection).
                  if (el && el.selectionStart === el.selectionEnd) {
                    const cursor = el.selectionStart ?? value.length;
                    const token = commandTokenAt(cursor);
                    if (token && cursor <= token.end) {
                      event.preventDefault();
                      deleteCommandToken(cursor);
                      return;
                    }
                  }
                }
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  onSend();
                }
                if (event.key === "Escape") {
                  setShowCommands(false);
                }
              }}
              placeholder={t("chat.placeholder")}
              disabled={disabled}
            />
          </div>

          {references.length > 0 && (
            <div className="composer__references">
              {references.map((reference) => (
                <span className="reference-chip" key={reference.id}>
                  <span className="reference-chip__title">{reference.title}</span>
                  <span className="reference-chip__id">{reference.id.slice(0, 8)}</span>
                  <button
                    type="button"
                    onClick={() => removeReference(reference.id)}
                    aria-label={t("chat.remove_reference")}
                  >
                    <X size={12} />
                  </button>
                </span>
              ))}
            </div>
          )}

          {attachments.length > 0 && (
            <div className="composer__attachments">
              {attachments.map((attachment) => (
                <span className="attachment-chip" key={attachment.id}>
                  {attachment.name}
                  <button
                    type="button"
                    onClick={() => removeAttachment(attachment.id)}
                    aria-label={t("chat.remove_attachment")}
                  >
                    <X size={12} />
                  </button>
                </span>
              ))}
            </div>
          )}

          <div className="composer__input-actions">
            <div className="composer__tools">
              <input ref={fileInputRef}type="file" multiple className="composer__file-input" onChange={(event) => addFiles(event.target.files)}/>
              <Tooltip content={t("chat.attach_tooltip")}>
                <Button variant="icon" onClick={() => fileInputRef.current?.click()} aria-label={t("chat.attach_tooltip")}>
                  <Paperclip size={19} />
                </Button>
              </Tooltip>
              <div className="composer__toolbar">
                <div className="composer__meta">
                  <div className="composer__select">
                    <span>{t("chat.model")}</span>
                    <Select value={selectedModel}onValueChange={onModelChange}disabled={modelOptions.length === 0}>
                      <SelectTrigger className="composer__model-trigger" size="sm">
                        <SelectValue placeholder={t("chat.model_unselected")} />
                      </SelectTrigger>
                      <SelectContent position="popper" align="start">
                        {modelOptions.map((model) => (<SelectItem key={model.id} value={model.id}>{model.provider? `${model.provider} · ${model.label}`: model.label}</SelectItem>))}
                      </SelectContent>
                    </Select>
                  </div>

                  <Tooltip content={t(workMode === "plan" ? "chat.work_plan_tip" : "chat.work_build_tip")}>
                    <button type="button" className="composer-toggle-button" onClick={() => onWorkModeChange(workMode === "plan" ? "build" : "plan")} aria-label={t("chat.toggle_work_mode")}>
                      <ListChecks size={14} />
                      <span>{t(workMode === "plan" ? "chat.work_plan" : "chat.work_build")}</span>
                    </button>
                  </Tooltip>

                  <Tooltip content={t(`chat.autonomy_${autonomy}_tip`)}>
                    <button type="button" className="composer-toggle-button" onClick={() => onAutonomyChange(nextAutonomy)} aria-label={t("chat.toggle_autonomy")}>
                      <ShieldCheck size={14} />
                      <span>{t(`chat.autonomy_${autonomy}`)}</span>
                    </button>
                  </Tooltip>
                  {branchStatus && (
                    <span
                     style={{ display: "flex", alignItems: "center", gap: "4px" ,color: "#666666"}}
                      title={branchStatus.branch ? `当前分支（仅显示，不可编辑）：${branchStatus.branch}` : (branchStatus.isRepo ? "当前处于 detached HEAD（无分支名）" : "当前项目不是 git 仓库")}
                    >
                      <GitBranch size={14} />
                      <span>{branchStatus.branch ?? (branchStatus.isRepo ? "detached" : "no repo")}</span>
                    </span>
                  )}
                </div>
              </div>
            </div>

            {isThinking ? (
              <Button variant="secondary"className="composer__send-button composer__send-button--stop"onClick={onStop}aria-label={t("chat.stop")}>
                <Square size={15} fill="currentColor" />
              </Button>
            ) : (
              <Button variant="primary"className="composer__send-button"onClick={onSend}disabled={disabled || !canSend}aria-label={editing ? t("message.edit_save") : t("common.send")}>
                {editing ? <Check size={17} /> : <Send size={17} />}
              </Button>
            )}
          </div>
        </div>
      </CardSlot>

      {showCommands && displayedCommands.length > 0 && (
        <div className="slash-menu" ref={menuRef}>
          {displayedCommands.map((command, index) => (
            <button
              type="button"
              key={command}
              className={index === activeCommandIndex ? "slash-menu__item slash-menu__item--active" : "slash-menu__item"}
              onMouseEnter={() => setCommandIndex(index)}
              onClick={() => insertCommand(command)}
            >
              <span>{command}</span>
              <small>{t(`chat.command_${command.slice(1)}`)}</small>
            </button>
          ))}
        </div>
      )}
    </footer>
  );
}
