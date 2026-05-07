import { useState } from "react";
import { Command, Fingerprint, Shield, Tags, Waypoints } from "lucide-react";

import { RateTemplates } from "@/pages/Settings/RateTemplates";
import { ProxyManager } from "@/pages/Settings/ProxyManager";
import { DeviceProfileManager } from "@/pages/Settings/DeviceProfileManager";
import { CommandTemplates } from "@/pages/Settings/CommandTemplates";
import { AliasManagement } from "@/pages/Settings/AliasManagement";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

export function Templates() {
  const [tab, setTab] = useState<"rate" | "proxy" | "device" | "command" | "alias">("rate");

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">通用模板</h1>
        <p className="text-sm text-muted-foreground">
          把可复用模板集中维护，可被多个账号在账号详情里选用。
        </p>
      </div>

      <Tabs value={tab} onValueChange={(v) => setTab(v as typeof tab)}>
        <TabsList>
          <TabsTrigger value="rate" className="gap-1.5">
            <Shield className="h-4 w-4" /> 风控模板
          </TabsTrigger>
          <TabsTrigger value="proxy" className="gap-1.5">
            <Waypoints className="h-4 w-4" /> 网络代理模板
          </TabsTrigger>
          <TabsTrigger value="device" className="gap-1.5">
            <Fingerprint className="h-4 w-4" /> 设备标识模板
          </TabsTrigger>
          <TabsTrigger value="command" className="gap-1.5">
            <Command className="h-4 w-4" /> 自定义命令模板
          </TabsTrigger>
          <TabsTrigger value="alias" className="gap-1.5">
            <Tags className="h-4 w-4" /> 命令别名
          </TabsTrigger>
        </TabsList>

        <TabsContent value="rate">
          <RateTemplates />
        </TabsContent>
        <TabsContent value="proxy">
          <ProxyManager />
        </TabsContent>
        <TabsContent value="device">
          <DeviceProfileManager />
        </TabsContent>
        <TabsContent value="command">
          <CommandTemplates />
        </TabsContent>
        <TabsContent value="alias">
          <AliasManagement />
        </TabsContent>
      </Tabs>
    </div>
  );
}
