import { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { FlaskConical, Package2, SatelliteDish, Settings2 } from "lucide-react";

import { getFeatureMatrix } from "@/api/features";
import { Spinner } from "@/components/ui/misc";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Select } from "@/components/ui/select";
import { isPlatformFeature } from "@/lib/plugin-modes";

import { featureConfigPath } from "./_shared/featureConfig";

type Zone = "platform" | "builtin" | "remote" | "experimental";

export function PluginsHome() {
  const nav = useNavigate();
  const [searchParams] = useSearchParams();
  const [selectedAid, setSelectedAid] = useState<number | null>(null);
  const matrixQ = useQuery({
    queryKey: ["matrix"],
    queryFn: getFeatureMatrix,
  });

  const accounts = matrixQ.data?.accounts ?? [];
  const features = matrixQ.data?.features ?? [];

  useEffect(() => {
    if (accounts.length === 0) return;

    const accountParam = searchParams.get("account");
    const requestedAid = accountParam ? Number(accountParam) : NaN;
    const validRequestedAid =
      Number.isInteger(requestedAid) && accounts.some((a) => a.id === requestedAid);

    if (validRequestedAid) {
      setSelectedAid(requestedAid);
      return;
    }

    setSelectedAid((prev) => {
      if (prev !== null && accounts.some((a) => a.id === prev)) return prev;
      return accounts[0].id;
    });
  }, [accounts, searchParams]);

  const selectedAccount = accounts.find((a) => a.id === selectedAid) ?? null;

  const grouped = useMemo(() => {
    const zones: Record<Zone, typeof features> = {
      platform: [],
      builtin: [],
      remote: [],
      experimental: [],
    };

    for (const feature of features) {
      const forceExperimental = feature.key === "codex_image";
      if (forceExperimental || feature.experimental) {
        zones.experimental.push(feature);
      } else if (isPlatformFeature(feature)) {
        zones.platform.push(feature);
      } else if (feature.is_builtin) {
        zones.builtin.push(feature);
      } else {
        zones.remote.push(feature);
      }
    }

    return zones;
  }, [features]);

  if (matrixQ.isLoading) {
    return (
      <div className="flex h-[40vh] items-center justify-center">
        <Spinner className="text-primary" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>Plugins 中心</CardTitle>
          <CardDescription>
            统一收敛平台能力、内置插件、远程插件与实验性能力。
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="outline" onClick={() => nav("/plugins/templates")}>Templates</Button>
            <Button variant="outline" onClick={() => nav("/plugins/aliases")}>Aliases</Button>
            <Button variant="outline" onClick={() => nav("/plugins/scheduler")}>Scheduler</Button>
          </div>

          {accounts.length > 0 ? (
            <div className="flex flex-col items-stretch gap-2 sm:flex-row sm:items-center">
              <span className="text-sm text-muted-foreground">账号视角：</span>
              <Select
                value={selectedAid?.toString() ?? ""}
                onChange={(e) => setSelectedAid(Number(e.target.value))}
                className="w-full sm:w-64"
              >
                {accounts.map((a) => (
                  <option key={a.id} value={a.id}>{a.name}</option>
                ))}
              </Select>
            </div>
          ) : null}
        </CardContent>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <FeatureZone
          title="平台能力"
          hint="随 worker 启动的基础能力。"
          icon={<Settings2 className="h-4 w-4" />}
          features={grouped.platform}
          selectedAccountId={selectedAccount?.id}
          selectedFeatures={selectedAccount?.features ?? {}}
        />
        <FeatureZone
          title="内置插件"
          hint="内置在系统中的常规插件。"
          icon={<Package2 className="h-4 w-4" />}
          features={grouped.builtin}
          selectedAccountId={selectedAccount?.id}
          selectedFeatures={selectedAccount?.features ?? {}}
        />
        <FeatureZone
          title="远程插件"
          hint="通过仓库或远程渠道提供。"
          icon={<SatelliteDish className="h-4 w-4" />}
          features={grouped.remote}
          selectedAccountId={selectedAccount?.id}
          selectedFeatures={selectedAccount?.features ?? {}}
        />
        <FeatureZone
          title="实验性"
          hint="可能依赖非稳定能力；codex_image 固定归类在此。"
          icon={<FlaskConical className="h-4 w-4" />}
          features={grouped.experimental}
          selectedAccountId={selectedAccount?.id}
          selectedFeatures={selectedAccount?.features ?? {}}
        />
      </div>
    </div>
  );
}

function FeatureZone({
  title,
  hint,
  icon,
  features,
  selectedAccountId,
  selectedFeatures,
}: {
  title: string;
  hint: string;
  icon: React.ReactNode;
  features: Array<{
    key: string;
    display_name: string;
    version?: string | null;
  }>;
  selectedAccountId?: number;
  selectedFeatures: Record<string, string>;
}) {
  const nav = useNavigate();

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-base">
          {icon}
          {title}
          <Badge variant="secondary">{features.length}</Badge>
        </CardTitle>
        <CardDescription>{hint}</CardDescription>
      </CardHeader>
      <CardContent>
        {features.length === 0 ? (
          <p className="text-sm text-muted-foreground">暂无内容</p>
        ) : (
          <div className="space-y-2">
            {features.map((f) => {
              const status = selectedFeatures[f.key] ?? "disabled";
              const path = featureConfigPath(selectedAccountId, f.key);
              return (
                <div key={f.key} className="flex items-center justify-between rounded-md border p-2">
                  <div>
                    <div className="text-sm font-medium">{f.display_name}</div>
                    <div className="font-mono text-xs text-muted-foreground">{f.key}</div>
                  </div>
                  <div className="flex items-center gap-2">
                    <Badge variant={status === "active" ? "default" : "outline"}>
                      {status === "active" ? "已启用" : "未启用"}
                    </Badge>
                    {path ? (
                      <Button size="sm" variant="outline" onClick={() => nav(path)}>
                        配置
                      </Button>
                    ) : null}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
