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

const SLASH_COMMANDS = ["/help", "/new", "/clear", "/providers", "/settings"];
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
  const [showCommands, setShowCommands] = useState(false);
  const activeWorkspace = workspaceOptions.find(
    (option) => option.id === activeWorkspaceId,
  );
  const workspaceMissing = showWorkspacePicker && !activeWorkspace;
  const canSend =
    Boolean(value.trim() || attachments.length > 0) && !workspaceMissing;

  useEffect(() => {
    setShowCommands(value.trim().startsWith("/"));
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
    onChange(`${command} `);
    setShowCommands(false);
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
            <Textarea
              className="composer__input"
              value={value}
              onChange={(event) => onChange(event.target.value)}
              onPaste={(event) => void handlePaste(event)}
              onKeyDown={(event) => {
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

      {showCommands && (
        <div className="slash-menu">
          {SLASH_COMMANDS.map((command) => (
            <button
              type="button"
              key={command}
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
