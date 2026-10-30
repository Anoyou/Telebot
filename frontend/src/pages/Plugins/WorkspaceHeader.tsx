import { Boxes } from "lucide-react";

import { PageHeader } from "@/components/layout/PageScaffold";

import { PluginWorkspaceNav } from "./WorkspaceNav";

type PluginWorkspaceTab = "home" | "market" | "manage";

type PluginWorkspaceHeaderProps = {
  activeTab: PluginWorkspaceTab;
  guideActive?: boolean;
};

export function PluginWorkspaceHeader({
  activeTab,
  guideActive = false,
}: PluginWorkspaceHeaderProps) {
  return (
    <>
      <PageHeader
        icon={Boxes}
        title="插件"
        description="「启用与配置」按账号启停和配置插件；在「市场」发现新插件，在「已安装」更新、卸载和管理插件仓库。"
      />
      <PluginWorkspaceNav activeTab={activeTab} guideActive={guideActive} />
    </>
  );
}
