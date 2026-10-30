import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Boxes, PackagePlus, Store } from "lucide-react";

import { fetchPluginMarketCatalog, marketUpdateCount } from "@/api/pluginMarket";

import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";

type PluginWorkspaceTab = "home" | "market" | "manage";

interface PluginWorkspaceNavProps {
  activeTab: PluginWorkspaceTab;
  guideActive?: boolean;
}

export function PluginWorkspaceNav({
  activeTab,
  guideActive = false,
}: PluginWorkspaceNavProps) {
  const nav = useNavigate();
  const targets: Record<PluginWorkspaceTab, string> = {
    home: "/plugins",
    market: "/plugins/market",
    manage: "/plugins/manage?tab=plugins",
  };

  // 更新角标：与市场页共用缓存；旧服务器没有市场接口时静默不显示。
  const marketQ = useQuery({
    queryKey: ["plugin-market", "catalog"],
    queryFn: () => fetchPluginMarketCatalog(false),
    staleTime: 5 * 60 * 1000,
    retry: false,
  });
  const updates = marketUpdateCount(marketQ.data);

  return (
    <div className="flex flex-wrap items-center justify-center gap-2 sm:justify-start">
      <Tabs
        className="w-full sm:w-auto"
        value={activeTab}
        onValueChange={(value) => {
          const target = targets[value as PluginWorkspaceTab];
          if (target) nav(target);
        }}
      >
        <TabsList>
          <TabsTrigger value="home" className="gap-1.5">
            <Boxes className="h-4 w-4" />
            启用与配置
          </TabsTrigger>
          <TabsTrigger value="market" className="gap-1.5" data-testid="plugin-workspace-market-tab">
            <Store className="h-4 w-4" />
            市场
            {updates > 0 && (
              <span
                className="ml-0.5 rounded-full bg-primary px-1.5 text-[10px] font-semibold leading-4 text-primary-foreground"
                data-testid="plugin-market-update-count"
              >
                {updates}
              </span>
            )}
          </TabsTrigger>
          <TabsTrigger value="manage" className={`gap-1.5 ${guideActive ? "siri-glow" : ""}`}>
            <PackagePlus className="h-4 w-4" />
            已安装
          </TabsTrigger>
        </TabsList>
      </Tabs>
    </div>
  );
}
