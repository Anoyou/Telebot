// 插件市场详情（服务器 0.117.0 起有完整内容）：简介、截图 / 预览（使用说明与指令）、最新版本说明、权限大白话、
// 兼容性、官方 / 社区 / 未审核标识、稳定 / 测试通道、安装 / 更新 / 固定版本。
//
// 旧服务器（0.113.0–0.116.x）的目录没有这些字段：对应区块直接不显示，安装 / 更新 / 固定照常可用。
import { useState } from "react";
import { Download, ExternalLink, Lock, LockOpen, ShieldAlert, ShieldCheck } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { MetaBadge } from "@/components/ui/meta-badge";
import { Spinner } from "@/components/ui/misc";
import {
  KIND_LABELS,
  TRUST_DESCRIPTIONS,
  TRUST_LABELS,
  channelLabel,
  describePermission,
  hasMultipleChannels,
  marketChannels,
  renderUsage,
  type PluginMarketItem,
} from "@/api/pluginMarket";

export function trustTone(trust: string): "success" | "info" | "warn" {
  if (trust === "official") return "success";
  if (trust === "community") return "info";
  return "warn";
}

/** 图标：有 https 图片用图片（加载失败退回 emoji），否则 emoji。 */
export function MarketIcon({ item, size = "md" }: { item: PluginMarketItem; size?: "md" | "lg" }) {
  const [broken, setBroken] = useState(false);
  const box = size === "lg" ? "h-16 w-16 text-3xl rounded-xl" : "h-10 w-10 text-xl rounded-md";
  if (item.icon_url && !broken) {
    return (
      <img
        src={item.icon_url}
        alt=""
        aria-hidden
        loading="lazy"
        referrerPolicy="no-referrer"
        onError={() => setBroken(true)}
        className={`${box} shrink-0 bg-muted object-cover`}
      />
    );
  }
  return (
    <div className={`${box} flex shrink-0 items-center justify-center bg-muted`} aria-hidden>
      {item.icon || "🧩"}
    </div>
  );
}

function Section({ title, children, testId }: { title: string; children: React.ReactNode; testId?: string }) {
  return (
    <section className="space-y-1.5" data-testid={testId}>
      <h3 className="text-sm font-medium">{title}</h3>
      {children}
    </section>
  );
}

export function MarketDetailDialog({
  item,
  open,
  busy,
  serverVersion,
  onOpenChange,
  onInstall,
  onUpdate,
  onPin,
  onSwitchChannel,
}: {
  item: PluginMarketItem | null;
  open: boolean;
  busy: boolean;
  serverVersion?: string;
  onOpenChange: (open: boolean) => void;
  onInstall: (item: PluginMarketItem, channel: string | null) => void;
  onUpdate: (item: PluginMarketItem) => void;
  onPin: (item: PluginMarketItem, pinned: boolean) => void;
  onSwitchChannel: (item: PluginMarketItem, channel: string) => void;
}) {
  const [installChannel, setInstallChannel] = useState<string>("stable");
  if (!item) return null;
  const installed = item.installed;
  const channels = marketChannels(item);
  const multiChannel = hasMultipleChannels(item);
  const description = item.description || item.summary;
  const usage = renderUsage(item.usage);
  const commands = item.commands ?? [];
  const screenshots = item.screenshots ?? [];
  const summaryEn = item.summary_i18n?.en;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] max-w-2xl overflow-y-auto" data-testid={`plugin-market-detail-${item.key}`}>
        <DialogHeader>
          <div className="flex items-start gap-3 pr-6">
            <MarketIcon item={item} size="lg" />
            <div className="min-w-0 space-y-1">
              <DialogTitle className="flex flex-wrap items-center gap-1.5">
                {item.name.zh}
                <MetaBadge tone={trustTone(item.trust)} data-testid="plugin-market-detail-trust">
                  {TRUST_LABELS[item.trust] ?? item.trust}
                </MetaBadge>
                {item.channel && item.channel !== "stable" && (
                  <MetaBadge tone="warn">{channelLabel(item.channel)}</MetaBadge>
                )}
              </DialogTitle>
              <DialogDescription>
                {KIND_LABELS[item.kind] ?? item.kind} · v{item.version}
                {item.author ? ` · ${item.author}` : ""}
                {item.license ? ` · ${item.license}` : ""} · {item.source_name}
              </DialogDescription>
            </div>
          </div>
        </DialogHeader>

        <div className="space-y-4 text-sm">
          <div
            className={`flex items-start gap-2 rounded-md border p-2 text-xs ${
              item.trust === "unreviewed" ? "border-warning/50 bg-warning/5" : "bg-muted/40"
            }`}
            data-testid="plugin-market-detail-trust-note"
          >
            {item.trust === "official" ? (
              <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-success" />
            ) : (
              <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0 text-warning" />
            )}
            <span>{TRUST_DESCRIPTIONS[item.trust] ?? TRUST_DESCRIPTIONS.unreviewed}</span>
          </div>

          {description && (
            <Section title="简介">
              <p className="whitespace-pre-line text-muted-foreground">{description}</p>
              {summaryEn && <p className="text-xs text-muted-foreground">{summaryEn}</p>}
            </Section>
          )}

          {screenshots.length > 0 && (
            <Section title="截图" testId="plugin-market-detail-screenshots">
              <div className="flex gap-2 overflow-x-auto pb-1">
                {screenshots.map((url) => (
                  <a key={url} href={url} target="_blank" rel="noreferrer noopener" className="shrink-0">
                    <img
                      src={url}
                      alt={`${item.name.zh} 截图`}
                      loading="lazy"
                      referrerPolicy="no-referrer"
                      className="h-48 max-w-[18rem] rounded-md border object-contain"
                    />
                  </a>
                ))}
              </div>
            </Section>
          )}

          {(usage || commands.length > 0) && (
            <Section title={screenshots.length ? "怎么用" : "怎么用（预览）"} testId="plugin-market-detail-usage">
              {commands.length > 0 && (
                <div className="flex flex-wrap gap-1.5">
                  {commands.map((command) => (
                    <code key={command} className="rounded bg-muted px-1.5 py-0.5 text-xs">
                      ,{command}
                    </code>
                  ))}
                </div>
              )}
              {usage && <p className="whitespace-pre-line text-muted-foreground">{usage}</p>}
              <p className="text-xs text-muted-foreground">示例按默认指令前缀「,」显示，实际以账号设置为准。</p>
            </Section>
          )}

          {item.release_notes && (
            <Section
              title={`v${item.version} 更新内容${item.released_at ? `（${item.released_at}）` : ""}`}
              testId="plugin-market-detail-release-notes"
            >
              <div className="max-h-48 overflow-y-auto whitespace-pre-line rounded-md bg-muted/40 p-2 text-xs text-muted-foreground">
                {item.release_notes}
              </div>
            </Section>
          )}

          {item.permissions.length > 0 && (
            <Section title="需要的权限" testId="plugin-market-detail-permissions">
              <ul className="space-y-1">
                {item.permissions.map((permission) => (
                  <li key={permission} className="flex flex-wrap items-baseline gap-2">
                    <span>{describePermission(permission)}</span>
                    <code className="text-xs text-muted-foreground">{permission}</code>
                  </li>
                ))}
              </ul>
            </Section>
          )}

          <Section title="兼容性" testId="plugin-market-detail-compat">
            {item.compatible ? (
              <p className="text-muted-foreground">
                兼容当前服务器{serverVersion ? `（${serverVersion}）` : ""}
                {item.min_telepilot_version ? `，最低需要 ${item.min_telepilot_version}` : ""}
                {item.plugin_api ? `，Plugin API ${item.plugin_api}` : ""}。
              </p>
            ) : (
              <p className="text-destructive">{item.incompatible_reason || "与当前服务器不兼容"}</p>
            )}
            {installed && !installed.same_source && (
              <p className="text-xs text-muted-foreground">已安装的版本来自别的仓库，这里不提示更新，也不能切换通道。</p>
            )}
          </Section>

          {multiChannel && (
            <Section title="版本通道" testId="plugin-market-detail-channels">
              <p className="text-xs text-muted-foreground">
                稳定版适合日常使用；测试版能先用上新功能，但可能有问题，也不会自动更新。
              </p>
              <div className="space-y-1.5">
                {channels.map((channel) => {
                  const current = installed ? item.channel === channel.name : installChannel === channel.name;
                  return (
                    <div
                      key={channel.name}
                      className="flex flex-wrap items-center justify-between gap-2 rounded-md border p-2"
                      data-testid={`plugin-market-channel-${channel.name}`}
                    >
                      <span>
                        {channelLabel(channel.name)}
                        <span className="text-muted-foreground"> · {channel.version ? `v${channel.version}` : "版本未知"}</span>
                        {current && installed ? <MetaBadge className="ml-2" tone="success">当前</MetaBadge> : null}
                      </span>
                      {installed ? (
                        installed.same_source &&
                        !current && (
                          <Button
                            size="sm"
                            variant="outline"
                            disabled={busy || Boolean(item.pinned_version) || !item.compatible}
                            onClick={() => onSwitchChannel(item, channel.name)}
                            data-testid={`plugin-market-switch-${channel.name}`}
                          >
                            切换到{channelLabel(channel.name)}
                          </Button>
                        )
                      ) : (
                        <Button
                          size="sm"
                          variant={current ? "default" : "outline"}
                          aria-pressed={current}
                          onClick={() => setInstallChannel(channel.name)}
                          data-testid={`plugin-market-pick-${channel.name}`}
                        >
                          {current ? "已选择" : "选择"}
                        </Button>
                      )}
                    </div>
                  );
                })}
              </div>
              {item.pinned_version && installed && (
                <p className="text-xs text-muted-foreground">已固定版本，先取消固定才能切换通道。</p>
              )}
            </Section>
          )}

          <div className="flex flex-wrap gap-2 border-t pt-3">
            {!installed ? (
              <Button
                onClick={() => onInstall(item, multiChannel && installChannel !== "stable" ? installChannel : null)}
                disabled={busy || !item.compatible}
                data-testid="plugin-market-detail-install"
              >
                {busy ? <Spinner className="h-4 w-4" /> : <Download className="h-4 w-4" />}
                安装{multiChannel && installChannel !== "stable" ? `（${channelLabel(installChannel)}）` : ""}
              </Button>
            ) : item.update_available ? (
              <Button
                onClick={() => onUpdate(item)}
                disabled={busy || Boolean(item.pinned_version)}
                data-testid="plugin-market-detail-update"
              >
                {busy ? <Spinner className="h-4 w-4" /> : <Download className="h-4 w-4" />}
                更新到 v{item.update_version || item.version}
              </Button>
            ) : (
              <MetaBadge tone="success">已安装 v{installed.version}，已是最新</MetaBadge>
            )}
            {installed && installed.same_source && (
              <Button
                variant="outline"
                onClick={() => onPin(item, !item.pinned_version)}
                data-testid="plugin-market-detail-pin"
              >
                {item.pinned_version ? <LockOpen className="h-4 w-4" /> : <Lock className="h-4 w-4" />}
                {item.pinned_version ? `取消固定（${item.pinned_version}）` : "固定版本"}
              </Button>
            )}
            {item.homepage && (
              <Button variant="ghost" asChild>
                <a href={item.homepage} target="_blank" rel="noreferrer noopener" data-testid="plugin-market-detail-homepage">
                  <ExternalLink className="h-4 w-4" />
                  插件主页
                </a>
              </Button>
            )}
          </div>
          <p className="text-xs text-muted-foreground">
            固定版本后，“全部更新”和自动更新都会跳过它。安装或更新后会观察一段时间，出问题自动回滚到旧版本。
          </p>
        </div>
      </DialogContent>
    </Dialog>
  );
}
