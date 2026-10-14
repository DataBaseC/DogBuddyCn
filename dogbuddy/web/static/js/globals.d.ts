// DogBuddyCn 控制台 · 全局命名空间声明（仅供 tsc checkJs 类型检查用，不参与运行）
// 各前端模块挂在 window.* 上（window.UI / window.API / window.App /
// window.DogBuddyViews / window.FileMgr），这里给它们补类型，避免 noEmit 检查
// 报 TS2339 / TS2304。运行时不需要本文件。

interface DogBuddyViewsMap {
  [name: string]: any;
}

interface SseBus {
  on(type: string, fn: Function): Function;
  off(type: string, fn: Function): void;
  start(): void;
  stop(): void;
  state: { connected: boolean; received: number; lastType: string; lastAt: string };
}

interface Window {
  UI: any;
  API: any;
  App: any;
  DogBuddyViews: DogBuddyViewsMap;
  FileMgr: any;
  SSE: SseBus;
}

declare const UI: any;
declare const API: any;
declare const App: any;
declare const DogBuddyViews: DogBuddyViewsMap;
declare const FileMgr: any;
declare const SSE: SseBus;
