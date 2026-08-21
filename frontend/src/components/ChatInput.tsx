import {
  AlertTriangle,
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
  Users,
  X,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ClipboardEvent, type DragEvent } from "react";
import { t } from "../lib/i18n";
import type { Autonomy, ComposerAttachment, OrgRosterEntry, SessionReference, WorkMode } from "../types";
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
import { Tooltip } from "./ui/tooltip";
import { ContextMenu } from "./ui/context-menu";
import { SidebarScrollbar } from "./ui/sidebar-scrollbar";
import { TypeCapsule, TYPE_CAPSULE_LABELS, type SlashCommandType } from "./ui/type-capsule";

/** A committed command chip rendered inline at the start of the composer text. */
export interface CommandChip {
  command: string;
  type: SlashCommandType;
  /** Skill package name when the chip is a skill sub-command (e.g. /cmd of pkg). */
  packageName?: string;
}

export interface ModelOption {
  id: string;
  label: string;
  provider?: string;
  contextError?: string;
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
  /** Queue the current composer content; auto-send after the stream finishes. */
  onSendQueued?: () => void;
  workMode: WorkMode;
  autonomy: Autonomy;
  selectedModel: string;
  attachments: ComposerAttachment[];
  /** 文件体积上限（MB），来自设置页，控制二进制附件内联字节的阈值 */
  maxAttachmentMb: number;
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
  /** 新对话草稿态：在 composer 顶部显示 workspace 选择器 */
  showWorkspacePicker?: boolean;
  workspaceOptions?: WorkspaceOption[];
  activeWorkspaceId?: string;
  onSelectWorkspace?: (projectId: string) => void;
  onCreateWorkspace?: () => void;
  /** 新对话草稿态：在 workspace 选择器旁显示 agent 选择器（默认 default_agent） */
  agentOptions?: OrgRosterEntry[];
  activeAgentId?: string;
  onSelectAgent?: (agentId: string) => void;
  /** Installed skills, used to populate the "/" command card. Each skill may
   *  declare sub-commands that show up as direct "/<command>" entries. */
  skills?: Array<{
    name: string;
    description?: string;
    enabled?: boolean;
    commands?: Array<{ name: string; description?: string }>;
  }>;
  /** Called when the "/" command menu opens, so the parent can refresh the skill list. */
  onOpenCommands?: () => void;
  /** The committed command chip (a real inline element at the start of the editor). */
  commandChip?: CommandChip | null;
  /** Called when the user commits or removes the command chip. */
  onCommandCommit?: (chip: CommandChip | null) => void;
}

const SLASH_COMMANDS = ["/help", "/new", "/clear", "/providers", "/skills", "/settings", "/memory"];

interface SlashCommandItem {
  command: string;
  description?: string;
  packageName?: string;
  type: SlashCommandType;
}
const MAX_ATTACHMENT_CHARS = 120_000;
// 二进制附件内联字节的体积上限由设置页的「文件体积上限」控制（默认 25MB），
// 经 maxAttachmentMb prop 传入。超过则只保留元信息、不内联，由后端在提示词中
// 如实说明「过大未内联」。文本附件不受此限（按字符截断）。

function arrayBufferToBase64(buffer: ArrayBuffer): string {
  let binary = "";
  const bytes = new Uint8Array(buffer);
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
  }
  return btoa(binary);
}

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

async function buildAttachment(file: File, maxBytes: number): Promise<ComposerAttachment> {
  const base = {
    id: `${file.name}-${file.size}-${Date.now()}-${Math.random().toString(16).slice(2)}`,
    name: file.name,
    size: file.size,
    type: file.type || "file",
  };
  // 超过体积上限：直接拒绝添加，不再读取字节（避免对大文件做无意义的 IO）。
  if (file.size > maxBytes) {
    return {
      ...base,
      binary: true,
      tooLarge: true,
      rejected: true,
      error: `文件过大（${(file.size / 1024 / 1024).toFixed(1)} MB），超过 ${(maxBytes / 1024 / 1024).toFixed(0)} MB 上限`,
    };
  }
  // 文本附件：读取原始文本，直接内联进提示词。
  if (isTextAttachment(file)) {
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
        rejected: true,
        error: error instanceof Error ? error.message : "Unable to read attachment",
      };
    }
  }
  // 二进制附件（图片/PDF/压缩包等）：一律读成 base64 data URL 一并发出，
  // 由后端原样转发给 LLM，LLM 自行决定是否受理。超限已在上方拦截。
  try {
    const buffer = await file.arrayBuffer();
    const dataUrl = `data:${file.type || "application/octet-stream"};base64,${arrayBufferToBase64(buffer)}`;
    return { ...base, content: dataUrl, binary: true };
  } catch (error) {
    return {
      ...base,
      binary: true,
      rejected: true,
      error: error instanceof Error ? error.message : "Unable to read attachment",
    };
  }
}

export function ChatInput({
  value,
  disabled,
  isThinking,
  onSendQueued,
  workMode,
  autonomy,
  selectedModel,
  attachments,
  maxAttachmentMb,
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
  showWorkspacePicker = false,
  workspaceOptions = [],
  activeWorkspaceId,
  onSelectWorkspace,
  onCreateWorkspace,
  agentOptions = [],
  activeAgentId,
  onSelectAgent,
  skills = [],
  onOpenCommands,
  commandChip = null,
  onCommandCommit,
}: ChatInputProps) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const editorRef = useRef<HTMLDivElement>(null);
  const isComposingRef = useRef(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const [showCommands, setShowCommands] = useState(false);
  const [commandIndex, setCommandIndex] = useState(0);
  const [addError, setAddError] = useState<string | null>(null);
  const addErrorTimer = useRef<number | null>(null);
  const [contextMenu, setContextMenu] = useState<{ x: number; y: number } | null>(null);

  const showAddError = (message: string) => {
    setAddError(message);
    if (addErrorTimer.current) window.clearTimeout(addErrorTimer.current);
    addErrorTimer.current = window.setTimeout(() => setAddError(null), 4000);
  };

  const currentModelError = modelOptions.find((option) => option.id === selectedModel)?.contextError;

  // 拖拽上传：只有真正携带文件时才激活高亮，避免文字拖拽被误拦截
  const dragCounter = useRef(0);
  const [dragActive, setDragActive] = useState(false);

  const hasFiles = (event: DragEvent) =>
    Boolean(event.dataTransfer && Array.from(event.dataTransfer.types).includes("Files"));

  const handleDragEnter = (event: DragEvent) => {
    if (!hasFiles(event)) return;
    event.preventDefault();
    dragCounter.current += 1;
    setDragActive(true);
  };

  const handleDragOver = (event: DragEvent) => {
    if (!hasFiles(event)) return;
    event.preventDefault();
    if (event.dataTransfer) event.dataTransfer.dropEffect = "copy";
  };

  const handleDragLeave = (event: DragEvent) => {
    if (!hasFiles(event)) return;
    dragCounter.current -= 1;
    if (dragCounter.current <= 0) {
      dragCounter.current = 0;
      setDragActive(false);
    }
  };

  const handleDrop = (event: DragEvent) => {
    if (!hasFiles(event)) return;
    event.preventDefault();
    dragCounter.current = 0;
    setDragActive(false);
    void addFiles(event.dataTransfer.files);
  };

  useEffect(() => {
    return () => {
      if (addErrorTimer.current) window.clearTimeout(addErrorTimer.current);
    };
  }, []);
  const activeWorkspace = workspaceOptions.find(
    (option) => option.id === activeWorkspaceId,
  );
  const workspaceMissing = showWorkspacePicker && !activeWorkspace;
  const activeAgent = agentOptions.find((entry) => entry.id === activeAgentId);
  const canSend =
    Boolean(value.trim() || attachments.length > 0 || Boolean(commandChip)) && !workspaceMissing;

  useEffect(() => {
    if (commandChip) {
      setShowCommands(false);
      return;
    }
    const nonWs = value.search(/\S/);
    const startsSlash = nonWs === 0 && value.charAt(0) === "/";
    const firstToken = startsSlash ? value.slice(1).split(/\s/)[0] ?? "" : "";
    const commandCommitted = startsSlash && value.length > firstToken.length + 1;
    // Pop only while a NEW leading command is being typed (starts with "/" and
    // no whitespace after it yet). A committed command (followed by whitespace)
    // closes the menu; mid-string "/" never pops (industry: commands at start).
    // "/skill" is a legacy typed command, not a menu entry — never pop for it.
    if (commandCommitted || !startsSlash || firstToken === "skill") {
      setShowCommands(false);
    } else {
      setShowCommands(true);
      setCommandIndex(0);
    }
  }, [value, commandChip]);

  // When the "/" menu opens, ask the parent to refresh the installed-skill
  // catalog so a skill installed via chat in a previous turn shows up here.
  useEffect(() => {
    if (showCommands) onOpenCommands?.();
  }, [showCommands, onOpenCommands]);

  // Installed skills become direct "/<name>" entries in the command card (the
  // "skill" keyword is dropped: "/skill ego-browser" -> "/ego-browser").
  const skillCommandItems = useMemo<SlashCommandItem[]>(
    () =>
      (skills ?? [])
        .filter((skill) => skill.enabled !== false)
        .map((skill) => ({ command: `/${skill.name}`, description: skill.description ?? "", type: "skill" })),
    [skills],
  );
  // Each skill's sub-commands become direct "/<command>" entries, tagged with
  // the owning package name so the menu reads "command · package · description".
  const skillSubCommandItems = useMemo<SlashCommandItem[]>(
    () =>
      (skills ?? [])
        .filter((skill) => skill.enabled !== false)
        .flatMap((skill) =>
          (skill.commands ?? []).map((cmd) => ({
            command: `/${cmd.name}`,
            description: cmd.description ?? "",
            packageName: skill.name,
            type: "skill" as const,
          })),
        ),
    [skills],
  );
  const staticCommandItems = SLASH_COMMANDS.map((command) => ({
    command,
    description: t(`chat.command_${command.slice(1)}`),
    type: "sys" as const,
  }));
  const commandItems = useMemo<SlashCommandItem[]>(
    () => [...staticCommandItems, ...skillSubCommandItems, ...skillCommandItems],
    [staticCommandItems, skillSubCommandItems, skillCommandItems],
  );

  // Leading slash token (text after the leading "/", up to whitespace) for
  // filtering — only the leading command is a real command. The menu filters
  // dynamically as the user types: a query matches the command name, the
  // package name, the description, or the type capsule (case-insensitive).
  // When nothing matches, fall back to showing ALL commands so the card still
  // pops (e.g. "/" inserted at the head of existing text).
  const nonWs = value.search(/\S/);
  const commandQuery = nonWs === 0 && value.charAt(0) === "/" ? value.slice(1).split(/\s/)[0] ?? "" : "";
  const queryLower = commandQuery.toLowerCase();
  const filteredItems = commandItems.filter((item) => {
    if (!queryLower) return true;
    const haystack = `${item.command.slice(1)} ${item.packageName ?? ""} ${item.description ?? ""} ${item.type}`.toLowerCase();
    return haystack.includes(queryLower);
  });
  const displayedItems = filteredItems.length > 0 ? filteredItems : commandItems;
  const activeCommandIndex = Math.min(commandIndex, Math.max(0, displayedItems.length - 1));

  useEffect(() => {
    if (!showCommands) return;
    const active = menuRef.current?.querySelector(".slash-menu__item--active");
    active?.scrollIntoView({ block: "nearest" });
  }, [activeCommandIndex, showCommands]);

  async function addFiles(files: FileList | null) {
    if (!files || files.length === 0) return;
    const maxBytes = Math.max(1, maxAttachmentMb) * 1024 * 1024;
    const fileList = Array.from(files);
    const current = attachments;
    // 乐观占位：先显示「读取中」，读完后替换，避免大文件读取时无任何反馈。
    const placeholders: ComposerAttachment[] = fileList.map((file) => ({
      id: `${file.name}-${file.size}-${Date.now()}-${Math.random().toString(16).slice(2)}`,
      name: file.name,
      size: file.size,
      type: file.type || "file",
      uploading: true,
    }));
    const placeholderIds = new Set(placeholders.map((placeholder) => placeholder.id));
    onAttachmentsChange([...current, ...placeholders]);

    const built = await Promise.all(fileList.map((file) => buildAttachment(file, maxBytes)));
    const failures = built.filter((attachment) => attachment.rejected);
    const successes = built.filter((attachment) => !attachment.rejected);
    // 用解析结果替换占位 chip（失败的不会进入发送列表）
    onAttachmentsChange([
      ...current.filter((attachment) => !placeholderIds.has(attachment.id)),
      ...successes,
    ]);

    if (failures.length > 0) {
      const limitMb = (maxBytes / 1024 / 1024).toFixed(0);
      const shown = failures.map((attachment) => attachment.name).slice(0, 3).join("、");
      const extra = failures.length > 3 ? ` 等 ${failures.length} 个` : "";
      showAddError(`附件添加失败：${shown}${extra} 超过 ${limitMb} MB 上限`);
    }
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

  function handlePaste(event: ClipboardEvent<HTMLDivElement>) {
    const pasted = event.clipboardData.getData("text/plain");
    if (!pasted) return;
    // contentEditable would paste rich HTML by default — force plain text so
    // the composer keeps a single text node (readable + IME-safe).
    event.preventDefault();
    if (extractSessionIds(pasted).length > 0) {
      void addReferenceFromText(pasted);
    }
    // Pasting over a selection that includes the chip would strip the chip span
    // out from under React — drop it through the parent first, then paste.
    const editor = editorRef.current;
    const chip = editor?.querySelector("[data-command-chip]");
    const selection = window.getSelection();
    if (editor && chip && selection && !selection.isCollapsed && selection.containsNode(chip, true)) {
      onCommandCommit?.(null);
      onChange?.("");
      Array.from(editor.childNodes).forEach((node) => {
        if (node.nodeType === Node.ELEMENT_NODE && (node as HTMLElement).dataset.commandChip !== undefined) return;
        editor.removeChild(node);
      });
    }
    document.execCommand("insertText", false, pasted);
  }

  function removeReference(id: string) {
    onReferencesChange(references.filter((reference) => reference.id !== id));
  }

  /** Read the composer text, skipping the (non-editable) command chip. Block
   *  elements and <br> are normalised back to newlines. */
  function getPromptText(editor: HTMLElement): string {
    const parts: string[] = [];
    const collect = (parent: Node, out: string[]) => {
      parent.childNodes.forEach((child) => {
        if (child.nodeType === Node.TEXT_NODE) {
          out.push(child.nodeValue ?? "");
          return;
        }
        if (child.nodeType !== Node.ELEMENT_NODE) return;
        const el = child as HTMLElement;
        if (el.dataset.commandChip !== undefined) return;
        if (el.tagName === "BR") {
          out.push("\n");
          return;
        }
        collect(child, out);
        if (/^(DIV|P|LI)$/.test(el.tagName)) out.push("\n");
      });
    };
    collect(editor, parts);
    return parts.join("");
  }

  /** Set the composer text, keeping the chip (if any) at the head. Only used
   *  for external value changes (edit-mode hydrate / send reset) — user typing
   *  never goes through React, so the caret never jumps. */
  function hydrateText(editor: HTMLElement, text: string) {
    Array.from(editor.childNodes).forEach((node) => {
      if (node.nodeType === Node.ELEMENT_NODE && (node as HTMLElement).dataset.commandChip !== undefined) return;
      editor.removeChild(node);
    });
    if (!text) return; // leave the editor truly empty so the placeholder shows
    const textNode = document.createTextNode(text);
    const chip = editor.querySelector("[data-command-chip]");
    if (chip) chip.after(textNode);
    else editor.appendChild(textNode);
  }

  /** Chrome leaves a <br>/empty <div> behind when a contentEditable is cleared.
   *  Normalise that back to a truly empty editor so the :empty placeholder
   *  shows and a fresh "/cmd" typed afterwards still auto-commits. */
  function normalizeEmptyEditor(editor: HTMLElement) {
    let empty = true;
    const walker = document.createTreeWalker(editor, NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) {
      if ((walker.currentNode as Text).nodeValue?.trim()) {
        empty = false;
        break;
      }
    }
    if (!empty) return;
    Array.from(editor.childNodes).forEach((node) => {
      if (node.nodeType === Node.ELEMENT_NODE && (node as HTMLElement).dataset.commandChip !== undefined) return;
      editor.removeChild(node);
    });
  }

  // Sync externally-driven `value` (edit mode / send reset) into the
  // uncontrolled editor, but never clobber the user's in-flight typing.
  useEffect(() => {
    const editor = editorRef.current;
    if (!editor) return;
    if (getPromptText(editor) === value) return;
    hydrateText(editor, value);
  }, [value]);

  // The chip is a React-managed child; React appends it, so re-anchor it to the
  // head of the editor after it renders, and pull any caret parked at the very
  // start (e.g. the commit-time caret landed before the chip rendered) to right
  // after the chip so subsequent typing continues the prompt.
  useEffect(() => {
    const editor = editorRef.current;
    if (!editor || !commandChip) return;
    const chip = editor.querySelector("[data-command-chip]");
    if (chip && editor.firstChild !== chip) editor.prepend(chip);
    // Re-focus after the chip renders so the caret stays active: for skill chips
    // the chip only mounts once an async validation resolves, so the focus we
    // applied at commit time is lost when this contentEditable re-renders.
    focusAfterChip();
  }, [commandChip]);

  /** Remove the leading "/token " text currently being typed (the raw command
   *  that a commit replaces with the chip). The token part is optional so a bare
   *  "/" typed before picking a command is also cleared. Returns whether any
   *  text was cut. */
  function stripLeadingCommand(editor: HTMLElement): boolean {
    const walker = document.createTreeWalker(editor, NodeFilter.SHOW_TEXT);
    const node = walker.nextNode();
    if (!node) return false;
    const text = node.nodeValue ?? "";
    const match = /^\/[A-Za-z0-9_.-]*\s?/.exec(text);
    if (!match) return false;
    const rest = text.slice(match[0].length);
    if (rest) node.nodeValue = rest;
    else node.parentNode?.removeChild(node);
    return true;
  }

  /** Place the caret right after the chip (start of the prompt text). */
  function focusAfterChip() {
    const editor = editorRef.current;
    if (!editor) return;
    editor.focus();
    const selection = window.getSelection();
    if (!selection) return;
    const range = document.createRange();
    const walker = document.createTreeWalker(editor, NodeFilter.SHOW_TEXT);
    const chip = editor.querySelector("[data-command-chip]");
    let firstText: Text | null = null;
    while (walker.nextNode()) {
      const candidate = walker.currentNode as Text;
      if (chip && chip.contains(candidate)) continue;
      firstText = candidate;
      break;
    }
    if (firstText) {
      range.setStart(firstText, 0);
      range.collapse(true);
    } else if (chip) {
      // chip is the only content — park the caret right after it so typing
      // continues the prompt instead of landing inside the chip or before it.
      range.setStartAfter(chip);
      range.collapse(true);
    } else if (editor.childNodes.length > 0) {
      const last = editor.lastChild as ChildNode;
      range.setStart(
        last,
        last.nodeType === Node.TEXT_NODE ? (last as Text).nodeValue?.length ?? 0 : last.childNodes.length,
      );
      range.collapse(true);
    } else {
      range.setStart(editor, 0);
      range.collapse(true);
    }
    selection.removeAllRanges();
    selection.addRange(range);
  }

  /** Commit a command: strip the typed leading token, lift the chip up to the
   *  parent (which validates/executes), and park the caret after the chip. */
  function commitCommand(item: SlashCommandItem | null) {
    if (!item) {
      setShowCommands(false);
      return;
    }
    const editor = editorRef.current;
    let stripped = false;
    if (editor && !commandChip) {
      stripped = stripLeadingCommand(editor);
    }
    setShowCommands(false);
    if (stripped && editor) onChange(getPromptText(editor));
    onCommandCommit?.({
      command: item.command,
      type: item.type,
      ...(item.packageName ? { packageName: item.packageName } : {}),
    });
    requestAnimationFrame(focusAfterChip);
  }

  /** A delete/cut/drag operation whose selection contains the chip would strip
   *  the chip span out from under React (it becomes a missing child → React
   *  crashes with "removeChild not a child" on the next unmount). Intercept and
   *  drop the chip through the parent instead, clearing the remaining text. */
  function handleBeforeInput(event: React.FormEvent<HTMLDivElement>) {
    const editor = editorRef.current;
    const chip = editor?.querySelector("[data-command-chip]");
    if (!editor || !chip) return;
    const native = event.nativeEvent as InputEvent;
    const inputType = native.inputType ?? "";
    if (!inputType.startsWith("delete") && inputType !== "deleteByCut") return;
    const selection = window.getSelection();
    if (!selection || selection.isCollapsed || !selection.containsNode(chip, true)) return;
    event.preventDefault();
    onCommandCommit?.(null);
    onChange?.("");
    Array.from(editor.childNodes).forEach((node) => {
      if (node.nodeType === Node.ELEMENT_NODE && (node as HTMLElement).dataset.commandChip !== undefined) return;
      editor.removeChild(node);
    });
  }

  function handleEditorInput() {
    const editor = editorRef.current;
    if (!editor) return;
    normalizeEmptyEditor(editor);
    const text = getPromptText(editor);
    // Auto-commit: a fully-typed known command token followed by a space turns
    // into the chip at once (no menu interaction needed).
    if (!commandChip && !isComposingRef.current) {
      const match = /^\/([A-Za-z0-9][A-Za-z0-9_.-]*)\s/.exec(text);
      if (match) {
        const item = commandItems.find((candidate) => candidate.command.slice(1) === match[1]);
        if (item) {
          commitCommand(item);
          return;
        }
      }
    }
    onChange(text);
  }

  function handleEditorKeyDown(event: React.KeyboardEvent<HTMLDivElement>) {
    const editor = editorRef.current;
    if (!editor) return;
    if (showCommands && displayedItems.length > 0) {
      if (event.key === "ArrowDown") {
        event.preventDefault();
        setCommandIndex((index) => (index + 1) % displayedItems.length);
        return;
      }
      if (event.key === "ArrowUp") {
        event.preventDefault();
        setCommandIndex((index) => (index - 1 + displayedItems.length) % displayedItems.length);
        return;
      }
      if (event.key === "Enter" && !event.shiftKey && !isComposingRef.current) {
        // Commit only on a genuine filter match; the fallback "show all" list
        // must not hijack unknown "/..." input (that falls through to send).
        if (filteredItems.length > 0) {
          event.preventDefault();
          commitCommand(displayedItems[activeCommandIndex] ?? displayedItems[0] ?? null);
          return;
        }
      }
    }
    if (event.key === "Backspace") {
      const selection = window.getSelection();
      const chip = editor.querySelector("[data-command-chip]");
      if (chip && selection && selection.isCollapsed) {
        const after = chip.nextSibling;
        const anchor = selection.anchorNode;
        const chipIndex = Array.prototype.indexOf.call(editor.childNodes, chip);
        const atStart =
          anchor === chip ||
          (after !== null && anchor === after && selection.anchorOffset === 0) ||
          (after !== null && after.contains(anchor as Node) && selection.anchorOffset === 0) ||
          (anchor === editor && selection.anchorOffset === chipIndex + 1);
        if (atStart) {
          event.preventDefault();
          onCommandCommit?.(null);
          return;
        }
      }
    }
    if (event.key === "Enter" && !event.shiftKey && !isComposingRef.current) {
      event.preventDefault();
      // Sync the latest DOM text into the parent before sending.
      onChange(getPromptText(editor));
      if (isThinking && !editing) {
        // Agent is streaming: default to queueing the message (it auto-sends
        // after the stream finishes). An empty composer does nothing.
        if (canSend) {
          onSendQueued?.();
        }
      } else if (!isThinking) {
        onSend();
      }
      return;
    }
    if (event.key === "Escape") {
      setShowCommands(false);
      return;
    }
    if (event.key === "Tab") {
      event.preventDefault();
      document.execCommand("insertText", false, "  ");
    }
  }

  const nextAutonomy: Autonomy = autonomy === "supervised" ? "guarded" : autonomy === "guarded" ? "autonomous" : "supervised";

  return (
    <footer className="composer">
      <CardSlot
        className={`composer__card${dragActive ? " composer__card--drag-active" : ""}`}
        onDragEnter={handleDragEnter}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
      >
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
            {agentOptions.length > 1 && (
              <DropdownMenu>
                <DropdownMenuTrigger
                  className={`composer__ws-chip ${activeAgent && activeAgent.status === 'disabled' ? "composer__ws-chip--empty" : ""}`}
                  aria-label={t("chat.agent_pick")}
                  title={t("chat.agent_pick")}
                >
                  <span className="composer__ws-icon">
                    <Users size={11} />
                  </span>
                  <span className="composer__ws-value">
                    {activeAgent
                      ? [activeAgent.name, activeAgent.role].filter(Boolean).join(" · ")
                      : t("chat.agent_pick")}
                  </span>
                  <ChevronDown size={13} className="composer__ws-chevron" />
                </DropdownMenuTrigger>
                <DropdownMenuContent align="start" className="composer__ws-menu">
                  <DropdownMenuLabel>{t("chat.agent_pick")}</DropdownMenuLabel>
                  {agentOptions.map((entry) => (
                    <DropdownMenuItem
                      key={entry.id}
                      className={`composer__ws-item ${entry.status === 'disabled' ? "composer__agent-item--disabled" : ""}`}
                      disabled={entry.status === 'disabled'}
                      onClick={() => onSelectAgent?.(entry.id)}
                    >
                      <Users size={14} />
                      <span className="composer__ws-item-text">
                        <span className="composer__ws-item-name">
                          {[entry.name, entry.role].filter(Boolean).join(" · ")}
                        </span>
                        {entry.team && (
                          <span className="composer__ws-item-path">{entry.team}</span>
                        )}
                      </span>
                      {entry.id === activeAgentId && (
                        <Check size={14} className="composer__ws-item-check" />
                      )}
                    </DropdownMenuItem>
                  ))}
                </DropdownMenuContent>
              </DropdownMenu>
            )}
            {activeWorkspace && (
              <span className="composer__ws-path" title={activeWorkspace.path}>
                {activeWorkspace.path}
              </span>
            )}
        </div>
      )}

        {currentModelError && (
          <div className="composer__warning-banner" role="alert">
            <AlertTriangle size={14} />
            <span>{t('chat.model_unreachable')}: {currentModelError}</span>
          </div>
        )}

        <div className="composer__input-box">
          <div
            ref={editorRef}
            className="composer__editor"
            contentEditable={!disabled}
            suppressContentEditableWarning
            data-placeholder={t("chat.placeholder")}
            onInput={handleEditorInput}
            onBeforeInput={handleBeforeInput}
            onPaste={handlePaste}
            onKeyDown={handleEditorKeyDown}
            onCompositionStart={() => { isComposingRef.current = true; }}
            onCompositionEnd={() => { isComposingRef.current = false; handleEditorInput(); }}
            onContextMenu={(event) => {
              if (disabled) return;
              event.preventDefault();
              setContextMenu({ x: event.clientX, y: event.clientY });
            }}
          >
            {commandChip && (
              <span
                className="composer__command-chip"
                contentEditable={false}
                data-command-chip=""
                data-cmd-type={commandChip.type}
                title={`${commandChip.command}（点击可更换命令）`}
                onClick={() => {
                  if (disabled) return;
                  setShowCommands(true);
                  setCommandIndex(0);
                }}
              >
                <span className={`type-capsule type-capsule--${commandChip.type} composer__command-chip__label`}>
                  {commandChip.command}
                </span>
                <span
                  className="composer__command-chip__x"
                  role="button"
                  aria-label={`移除命令 ${commandChip.command}`}
                  onClick={(event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    onCommandCommit?.(null);
                    editorRef.current?.focus();
                  }}
                >
                  ×
                </span>
              </span>
            )}
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

          {addError && (
            <div className="composer__add-error" role="alert">
              <AlertTriangle size={14} />
              <span>{addError}</span>
              <button type="button" onClick={() => setAddError(null)} aria-label={t("common.close")}>
                <X size={12} />
              </button>
            </div>
          )}

          {attachments.length > 0 && (
            <div className="composer__attachments">
              {attachments.map((attachment) => (
                <span
                  className={`attachment-chip${attachment.uploading ? " attachment-chip--uploading" : ""}${attachment.error ? " attachment-chip--error" : ""}`}
                  key={attachment.id}
                  title={attachment.error || undefined}
                >
                  {attachment.uploading && <span className="attachment-chip__spinner" aria-hidden />}
                  {attachment.name}
                  {attachment.error && !attachment.uploading && (
                    <span className="attachment-chip__error">· {attachment.error}</span>
                  )}
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

                  {workMode === "build" && (
                    <Tooltip content={t(`chat.autonomy_${autonomy}_tip`)}>
                      <button type="button" className="composer-toggle-button" onClick={() => onAutonomyChange(nextAutonomy)} aria-label={t("chat.toggle_autonomy")}>
                        <ShieldCheck size={14} />
                        <span>{t(`chat.autonomy_${autonomy}`)}</span>
                      </button>
                    </Tooltip>
                  )}
                  {branchStatus && (
                    <span
                     style={{ display: "flex", alignItems: "center", gap: "4px" ,color: "var(--muted-foreground)"}}
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
              // Agent is streaming. Priority: if the composer has content, show
              // the SEND button (defaults to queueing) and hide Stop; if it is
              // empty, show Stop so the running task can still be interrupted.
              canSend ? (
                <Button variant="primary" className="composer__send-button" onClick={onSendQueued} disabled={!canSend} aria-label={t("chat.send_queued")}>
                  <Send size={17} />
                </Button>
              ) : (
                <Button variant="secondary" className="composer__send-button composer__send-button--stop" onClick={onStop} aria-label={t("chat.stop")}>
                  <Square size={15} fill="currentColor" />
                </Button>
              )
            ) : (
              <Button variant="primary" className="composer__send-button" onClick={onSend} disabled={disabled || !canSend} aria-label={editing ? t("message.edit_save") : t("common.send")}>
                {editing ? <Check size={17} /> : <Send size={17} />}
              </Button>
            )}
          </div>
        </div>
        {dragActive && (
          <div className="composer__drop-overlay" aria-hidden>
            <div className="composer__drop-overlay-inner">
              <Paperclip size={28} />
              <span>{t("chat.drop_files_here")}</span>
            </div>
          </div>
        )}
      </CardSlot>

      {showCommands && displayedItems.length > 0 && (
        <SidebarScrollbar ref={menuRef} className="slash-menu">
          <div className="slash-menu-content">
            {displayedItems.map((item, index) => (
              <button
                type="button"
                key={`${item.type}:${item.command}:${item.packageName ?? ''}`}
                className={index === activeCommandIndex ? "slash-menu__item slash-menu__item--active" : "slash-menu__item"}
                onMouseEnter={() => setCommandIndex(index)}
                onClick={() => commitCommand(item)}
              >
              <TypeCapsule type={item.type} className="slash-menu__type">{TYPE_CAPSULE_LABELS[item.type]}</TypeCapsule>
              <span className="slash-menu__cmd">{item.command}</span>
                <small>
                  {item.packageName && <span className="slash-menu__pkg">{item.packageName}</span>}
                  {item.description || (item.type === "skill" ? "加载并运行此技能" : "")}
                </small>
              </button>
            ))}
          </div>
        </SidebarScrollbar>
      )}

      <ContextMenu
        open={contextMenu !== null}
        x={contextMenu?.x ?? 0}
        y={contextMenu?.y ?? 0}
        onClose={() => setContextMenu(null)}
        targetRef={editorRef}
        clipboardSlots={["copy", "cut", "paste", "selectAll", "delete", "clear"]}
        onSlotPaste={(text) => void addReferenceFromText(text)}
      />
    </footer>
  );
}
