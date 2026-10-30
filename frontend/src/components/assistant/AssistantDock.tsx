import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useLocation } from "react-router-dom";
import {
  agentAccountFromPath,
  agentAccountFromSearch,
  nextAssistantOutcomeSignal,
  shouldOpenAssistantDock,
  withoutAgentAccountSearch,
  type AgentPageAccount,
  type AssistantOutcomeSignal,
  type AssistantOutcomeStatus,
} from "./assistantDockState";

type AssistantDockValue = {
  collapsed: boolean;
  mounted: boolean;
  streaming: boolean;
  outcomeSignal: AssistantOutcomeSignal | null;
  setCollapsed: (collapsed: boolean) => void;
  setStreaming: (streaming: boolean) => void;
  notifyOutcome: (status: AssistantOutcomeStatus) => void;
  /**
   * 当前页面的账号上下文（第 20 轮）：插件页登记所选账号；账号详情 / 账号插件配置从路由推出。
   * 打开 Agent 时自动带入该账号。
   */
  pageAccount: AgentPageAccount | null;
  setRegisteredPageAccount: (account: AgentPageAccount | null) => void;
};

export const ASSISTANT_SURFACE_ID = "telepilot-assistant-surface";

const AssistantDockContext = createContext<AssistantDockValue | null>(null);

export function AssistantDockProvider({ children }: { children: ReactNode }) {
  const location = useLocation();
  const initiallyOpen = shouldOpenAssistantDock(location.pathname, location.search);
  const [collapsed, setCollapsedState] = useState(!initiallyOpen);
  const [mounted, setMounted] = useState(initiallyOpen);
  const [streaming, setStreaming] = useState(false);
  const [outcomeSignal, setOutcomeSignal] = useState<AssistantDockValue["outcomeSignal"]>(null);
  const [registeredPageAccount, setRegisteredPageAccount] = useState<AgentPageAccount | null>(null);
  // 带账号进入 Agent 的参数被 Agent 消费（从地址栏去掉）不算换页，否则 Dock 会随即收起。
  const locationKeyRef = useRef(
    `${location.pathname}${withoutAgentAccountSearch(location.search)}${location.hash}`,
  );
  const setCollapsed = useCallback((next: boolean) => {
    if (!next) {
      setMounted(true);
    }
    setCollapsedState(next);
    if (next) {
      window.requestAnimationFrame(() => {
        const triggers = Array.from(document.querySelectorAll<HTMLButtonElement>(
          "[data-assistant-mobile-button], [data-assistant-desktop-pet]",
        ));
        triggers.find((button) => button.offsetParent !== null)?.focus({ preventScroll: true });
      });
    }
  }, []);
  const notifyOutcome = useCallback((status: AssistantOutcomeStatus) => {
    setOutcomeSignal((current) => nextAssistantOutcomeSignal(current, status));
  }, []);

  useEffect(() => {
    const locationKey = `${location.pathname}${withoutAgentAccountSearch(location.search)}${location.hash}`;
    if (agentAccountFromSearch(location.search) != null) {
      // ?agent_account=…：从账号页 / 插件页「问 Agent」进入，展开 Dock
      locationKeyRef.current = locationKey;
      setMounted(true);
      setCollapsedState(false);
      return;
    }
    if (locationKey === locationKeyRef.current) return;
    locationKeyRef.current = locationKey;
    // 深链 /assistant?session=…：打开悬浮助手，不因路由切换而收起
    if (shouldOpenAssistantDock(location.pathname, location.search)) {
      setMounted(true);
      setCollapsedState(false);
      return;
    }
    if (!collapsed) setCollapsed(true);
  }, [collapsed, location.hash, location.pathname, location.search, setCollapsed]);

  const pathAccount = useMemo(() => agentAccountFromPath(location.pathname), [location.pathname]);
  const pageAccount = registeredPageAccount ?? pathAccount;

  const value = useMemo(
    () => ({
      collapsed,
      mounted,
      streaming,
      outcomeSignal,
      setCollapsed,
      setStreaming,
      notifyOutcome,
      pageAccount,
      setRegisteredPageAccount,
    }),
    [collapsed, mounted, notifyOutcome, outcomeSignal, pageAccount, setCollapsed, streaming],
  );

  return (
    <AssistantDockContext.Provider value={value}>
      {children}
    </AssistantDockContext.Provider>
  );
}

/**
 * 页面登记自己的账号上下文（插件页所选账号）；离开页面或账号变为空时清除。
 * 账号详情与账号插件配置页不必登记：Dock 会从路由 /accounts/:aid 推出。
 */
export function useAgentPageAccount(account: AgentPageAccount | null): void {
  const { setRegisteredPageAccount } = useAssistantDock();
  const id = account?.id ?? null;
  const source = account?.source ?? null;
  useEffect(() => {
    setRegisteredPageAccount(id != null && source ? { id, source } : null);
    return () => setRegisteredPageAccount(null);
  }, [id, setRegisteredPageAccount, source]);
}

export function useAssistantDock(): AssistantDockValue {
  const value = useContext(AssistantDockContext);
  if (!value) {
    throw new Error("useAssistantDock must be used inside AssistantDockProvider");
  }
  return value;
}
