/**
 * API 统一出口 —— 按链路模块化：
 *   chat(对话/工具/审批) · impersonation(角色扮演) · novels(导入/书目)
 *   characters(角色管线) · world(世界体系) · settings(LLM 配置)
 *   eval(评估) · health(健康) · jobs(作业轮询)
 */
export * as chat from './chat';
export * as impersonation from './impersonation';
export * as novels from './novels';
export * as characters from './characters';
export * as world from './world';
export * as settings from './settings';
export * as evalApi from './eval';
export * as health from './health';
export * as jobs from './jobs';
export { ApiError, request, qs, extractErrorDetail } from './http';
