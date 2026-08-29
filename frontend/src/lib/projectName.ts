import { t } from './i18n';

/** 系统保留的聊天项目固定 memory_dir（与后端 CHAT_MEMORY_DIR 一致）。 */
export const CHAT_MEMORY_DIR = '__chat__';

/** 系统保留的聊天项目：名称由前端本地化显示（后端只存固定兜底名）。 */
export function displayProjectName(project: { name: string; is_chat?: boolean }): string {
  return project.is_chat ? t('chat_project.name') : project.name;
}

/** 记忆库/树里的项目标签：按 memory_dir 识别聊天项目并本地化名称。 */
export function displayMemoryProjectName(project: { name: string; project_name?: string }): string {
  return project.name === CHAT_MEMORY_DIR
    ? t('chat_project.name')
    : project.project_name || project.name;
}
