import { t } from './i18n';

/** 系统保留的聊天项目：名称由前端本地化显示（后端只存固定兜底名）。 */
export function displayProjectName(project: { name: string; is_chat?: boolean }): string {
  return project.is_chat ? t('chat_project.name') : project.name;
}
