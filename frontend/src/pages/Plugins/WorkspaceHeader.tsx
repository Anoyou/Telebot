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
        description="按账号启用和配置插件；在「插件市场」发现新插件，在「插件管理」安装、更新和卸载。"
      />
      <PluginWorkspaceNav activeTab={activeTab} guideActive={guideActive} />
    </>
  );
}
