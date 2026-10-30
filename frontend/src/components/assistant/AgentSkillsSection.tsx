import { useState } from "react";
import type React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { FileDiff, Plus, Sparkles, SquarePen } from "lucide-react";
import {
  editAgentSkill,
  fileToBase64,
  getAgentSkill,
  getAgentSkillUpstream,
  installAgentSkill,
  keepAgentSkillModified,
  listAgentSkills,
  patchAgentSkill,
  restoreAgentSkillDefault,
  sourceLabel,
  uninstallAgentSkill,
  updateAgentSkill,
  type AgentSkill,
  type AgentSkillInstall,
} from "@/api/agentSkills";
import { getErrMsg } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/misc";
import { Select } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { AGENT_SKILLS_INTRO } from "@/lib/agentSkillOptions";
import { SkillDiffViewer } from "@/components/assistant/SkillDiffViewer";
import { cn } from "@/lib/utils";
import { confirmDialog } from "@/components/ui/confirm-dialog";

const QUERY_KEY = ["agent-skills"];
type Source = AgentSkillInstall["source"];

const SOURCE_HINTS: Record<Source, string> = {
  github: "GitHub 仓库或子目录链接，例如 https://github.com/anthropics/skills/tree/main/skills/pdf。私有仓库沿用插件仓库里保存的凭据。",
  zip_url: "公网 https 的 zip 下载链接（≤10MB）。",
  zip_upload: "上传 zip 文件（≤10MB），包内需有 SKILL.md。",
  md_upload: "上传单个 SKILL.md 文件（.md）。",
  paste: "直接粘贴 SKILL.md 全文（以 --- 包裹的 front matter 开头，含 name 与 description）。",
};

/** 助手页「Skills」按钮打开的独立对话框：标题与说明在对话框头部，正文是 {@link AgentSkillsSection}。 */
export function AgentSkillsDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        data-testid="agent-skills-dialog"
        className="max-h-[min(88dvh,52rem)] max-w-3xl grid-cols-[minmax(0,1fr)] grid-rows-[auto_minmax(0,1fr)] overflow-hidden"
      >
        <DialogHeader className="pr-6">
          <DialogTitle className="flex items-center gap-2">
            <Sparkles className="h-4 w-4 text-primary" aria-hidden />
            Skills
          </DialogTitle>
          <DialogDescription>{AGENT_SKILLS_INTRO}</DialogDescription>
        </DialogHeader>
        <div className="-mx-4 min-h-0 overflow-y-auto overscroll-contain px-4 sm:-mx-6 sm:px-6">
          <AgentSkillsSection />
        </div>
      </DialogContent>
    </Dialog>
  );
}

/** Agent Skills 管理：列表、启用范围、安装、编辑、更新、卸载。 */
export function AgentSkillsSection() {
  const qc = useQueryClient();
  const listQ = useQuery({ queryKey: QUERY_KEY, queryFn: listAgentSkills });
  const [installing, setInstalling] = useState(false);
  const [detailName, setDetailName] = useState<string | null>(null);
  const invalidate = () => void qc.invalidateQueries({ queryKey: QUERY_KEY });

  const patchMut = useMutation({
    mutationFn: (vars: { name: string; enabled_agent?: boolean; enabled_plugins?: boolean }) =>
      patchAgentSkill(vars.name, { enabled_agent: vars.enabled_agent, enabled_plugins: vars.enabled_plugins }),
    onSuccess: invalidate,
    onError: (error) => toast.error(`修改失败：${getErrMsg(error)}`),
  });

  const items = listQ.data?.items ?? [];
  const installed = items.filter((item) => !item.builtin);
  const builtin = items.filter((item) => item.builtin);

  return (
    <div className="pb-1" data-testid="agent-skills-section">
      <div className="flex items-center justify-between gap-2">
        <div className="text-sm font-medium">已安装（{installed.length}）</div>
        <Button type="button" size="sm" variant="outline" onClick={() => setInstalling(true)}>
          <Plus className="h-4 w-4" aria-hidden />
          安装 skill
        </Button>
      </div>
      {listQ.isLoading ? (
        <Skeleton className="mt-3 h-10 w-full" />
      ) : listQ.isError ? (
        <p className="mt-3 text-xs text-destructive">读取失败：{getErrMsg(listQ.error)}</p>
      ) : (
        <div className="mt-3 space-y-2">
          {installed.length === 0 ? <p className="text-xs text-muted-foreground">还没有安装 skill</p> : null}
          {installed.map((item) => (
            <SkillRow key={item.name} item={item} busy={patchMut.isPending} onPatch={(p) => patchMut.mutate({ name: item.name, ...p })} onOpen={() => setDetailName(item.name)} />
          ))}
          {builtin.length > 0 ? (
            <details className="rounded-md border border-border/60 px-3 py-2">
              <summary className="cursor-pointer text-xs text-muted-foreground">
                TelePilot 内置领域技能（{builtin.length}）：不可卸载，可对系统 Agent 停用
              </summary>
              <div className="mt-2 space-y-2">
                {builtin.map((item) => (
                  <SkillRow key={item.name} item={item} busy={patchMut.isPending} onPatch={(p) => patchMut.mutate({ name: item.name, ...p })} onOpen={() => setDetailName(item.name)} />
                ))}
              </div>
            </details>
          ) : null}
        </div>
      )}
      <InstallDialog open={installing} onOpenChange={setInstalling} onInstalled={invalidate} />
      {detailName ? <DetailDialog name={detailName} onClose={() => setDetailName(null)} onChanged={invalidate} /> : null}
    </div>
  );
}

function SkillRow({
  item,
  busy,
  onPatch,
  onOpen,
}: {
  item: AgentSkill;
  busy: boolean;
  onPatch: (patch: { enabled_agent?: boolean; enabled_plugins?: boolean }) => void;
  onOpen: () => void;
}) {
  return (
    <div className="rounded-md border border-border/70 bg-background/45 px-3 py-2.5">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <button type="button" className="min-w-0 flex-1 basis-48 text-left" onClick={onOpen}>
          <span className="block truncate font-mono text-sm font-medium">{item.name}</span>
          <span className="mt-0.5 line-clamp-2 block break-words text-xs text-muted-foreground">{item.description}</span>
        </button>
        <div className="flex min-w-0 max-w-full flex-wrap gap-1">
          <Badge variant="outline" className="max-w-full truncate">{sourceLabel(item)}</Badge>
          {item.user_modified ? <Badge variant="outline">已修改</Badge> : null}
          {item.upstream_changed ? <Badge>内置有新版本</Badge> : null}
        </div>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-border/45 pt-2 text-xs text-muted-foreground">
        <label className="flex items-center gap-1.5">
          <Switch className="scale-90" checked={item.enabled_agent} disabled={busy} onCheckedChange={(checked) => onPatch({ enabled_agent: checked })} aria-label={`${item.name} 对系统 Agent 启用`} />
          系统 Agent
        </label>
        {item.plugin_supported ? (
          <label className="flex items-center gap-1.5">
            <Switch className="scale-90" checked={item.enabled_plugins} disabled={busy} onCheckedChange={(checked) => onPatch({ enabled_plugins: checked })} aria-label={`${item.name} 对插件启用`} />
            插件可用
          </label>
        ) : (
          <span>仅系统 Agent</span>
        )}
      </div>
    </div>
  );
}

function InstallDialog({ open, onOpenChange, onInstalled }: { open: boolean; onOpenChange: (v: boolean) => void; onInstalled: () => void }) {
  const [source, setSource] = useState<Source>("github");
  const [url, setUrl] = useState("");
  const [ref, setRef] = useState("");
  const [subpath, setSubpath] = useState("");
  const [skillMd, setSkillMd] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [enabledAgent, setEnabledAgent] = useState(true);
  const [enabledPlugins, setEnabledPlugins] = useState(false);

  const mut = useMutation({
    mutationFn: async () => {
      const payload: AgentSkillInstall = { source, enabled_agent: enabledAgent, enabled_plugins: enabledPlugins };
      if (source === "github" || source === "zip_url") payload.url = url.trim();
      if (source === "github" && ref.trim()) payload.ref = ref.trim();
      if ((source === "github" || source === "zip_url" || source === "zip_upload") && subpath.trim()) payload.path = subpath.trim();
      if (source === "zip_upload" && file) payload.zip_base64 = await fileToBase64(file);
      if (source === "md_upload" && file) payload.skill_md = await file.text();
      if (source === "paste") payload.skill_md = skillMd;
      return installAgentSkill(payload);
    },
    onSuccess: (skill) => {
      toast.success(`已安装 ${skill.name}`);
      onInstalled();
      onOpenChange(false);
      setUrl("");
      setRef("");
      setSubpath("");
      setSkillMd("");
      setFile(null);
    },
    // 安装失败只提示，不自动重试；网络超时时结果未知，请刷新列表确认。
    onError: (error) => toast.error(`安装失败：${getErrMsg(error)}（如为超时，请刷新列表确认是否已安装）`),
  });

  const needsFile = source === "zip_upload" || source === "md_upload";
  const canSubmit =
    !mut.isPending &&
    ((source === "github" || source === "zip_url") ? url.trim().length > 0 : needsFile ? file != null : skillMd.trim().length > 0);

  return (
    <Dialog open={open} onOpenChange={(value) => !mut.isPending && onOpenChange(value)}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>安装 skill</DialogTitle>
          <DialogDescription>服务器会下载并校验：大小与文件数上限、路径安全、名称唯一、front matter 格式。</DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1.5">
            <Label htmlFor="skill-source">来源</Label>
            <Select id="skill-source" value={source} onChange={(event) => { setSource(event.target.value as Source); setFile(null); }}>
              <option value="github">GitHub 仓库 / 子目录</option>
              <option value="zip_url">zip 下载链接</option>
              <option value="zip_upload">上传 zip</option>
              <option value="md_upload">上传 SKILL.md 文件</option>
              <option value="paste">粘贴 SKILL.md</option>
            </Select>
            <p className="text-xs text-muted-foreground">{SOURCE_HINTS[source]}</p>
          </div>
          {source === "github" || source === "zip_url" ? (
            <Input placeholder={source === "github" ? "https://github.com/用户/仓库/tree/分支/子目录" : "https://…/skill.zip"} value={url} onChange={(event) => setUrl(event.target.value)} />
          ) : null}
          {source === "github" ? <Input placeholder="分支/标签/提交（可选，覆盖链接里的分支）" value={ref} onChange={(event) => setRef(event.target.value)} /> : null}
          {source === "github" || source === "zip_url" || source === "zip_upload" ? (
            <Input placeholder="skill 子目录（可选，包里有多个 skill 时必填）" value={subpath} onChange={(event) => setSubpath(event.target.value)} />
          ) : null}
          {needsFile ? (
            <Input type="file" accept={source === "zip_upload" ? ".zip,application/zip" : ".md,text/markdown,text/plain"} onChange={(event) => setFile(event.target.files?.[0] ?? null)} />
          ) : null}
          {source === "paste" ? (
            <Textarea rows={10} className="font-mono text-xs" placeholder={"---\nname: my-skill\ndescription: 这个 skill 做什么、什么时候用\n---\n\n# 说明…"} value={skillMd} onChange={(event) => setSkillMd(event.target.value)} />
          ) : null}
          <div className="flex flex-wrap gap-4 text-sm">
            <label className="flex items-center gap-2"><Switch checked={enabledAgent} onCheckedChange={setEnabledAgent} />对系统 Agent 启用</label>
            <label className="flex items-center gap-2"><Switch checked={enabledPlugins} onCheckedChange={setEnabledPlugins} />对插件启用</label>
          </div>
        </div>
        <DialogFooter>
          <Button type="button" variant="ghost" disabled={mut.isPending} onClick={() => onOpenChange(false)}>取消</Button>
          <Button type="button" disabled={!canSubmit} onClick={() => mut.mutate()}>{mut.isPending ? "正在安装…" : "安装"}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

type DetailTab = "edit" | "diff";

function DetailDialog({ name, onClose, onChanged }: { name: string; onClose: () => void; onChanged: () => void }) {
  const qc = useQueryClient();
  const detailQ = useQuery({ queryKey: [...QUERY_KEY, name], queryFn: () => getAgentSkill(name) });
  const [draft, setDraft] = useState<string | null>(null);
  const [tab, setTab] = useState<DetailTab>("edit");
  const detail = detailQ.data;
  const showDiff = tab === "diff" && !!detail?.bundled;
  const upstreamQ = useQuery({
    queryKey: [...QUERY_KEY, name, "upstream"],
    queryFn: () => getAgentSkillUpstream(name),
    enabled: showDiff && detail?.source_type === "plugin_bundled",
  });
  const refresh = () => {
    onChanged();
    void qc.invalidateQueries({ queryKey: [...QUERY_KEY, name] });
  };
  const onError = (error: unknown) => toast.error(getErrMsg(error));
  const saveMut = useMutation({ mutationFn: () => editAgentSkill(name, draft ?? ""), onSuccess: () => { toast.success("已保存"); setDraft(null); refresh(); }, onError });
  const updateMut = useMutation({ mutationFn: () => updateAgentSkill(name), onSuccess: (r) => { toast.success(r.changed ? "已更新到最新内容" : "已是最新"); refresh(); }, onError });
  const restoreMut = useMutation({ mutationFn: () => restoreAgentSkillDefault(name), onSuccess: () => { toast.success("已恢复默认"); setTab("edit"); refresh(); }, onError });
  const keepMut = useMutation({ mutationFn: () => keepAgentSkillModified(name), onSuccess: () => { toast.success("已保留你的修改"); refresh(); }, onError });
  const uninstallMut = useMutation({
    mutationFn: () => uninstallAgentSkill(name),
    onSuccess: (r) => { toast.success(`已卸载${r.unbound.length ? `，并从 ${r.unbound.length} 处插件配置解绑` : ""}`); onChanged(); onClose(); },
    onError,
  });
  const busy = saveMut.isPending || updateMut.isPending || restoreMut.isPending || keepMut.isPending || uninstallMut.isPending;
  const dirty = draft != null && draft !== detail?.skill_md;

  return (
    <Dialog open onOpenChange={(value) => !value && !busy && onClose()}>
      {/* grid 列用 minmax(0,1fr)：长行不会把列撑出对话框；正文区独立滚动，页头与按钮常驻。 */}
      <DialogContent
        data-testid="agent-skill-detail"
        className="max-h-[min(92dvh,60rem)] max-w-5xl grid-cols-[minmax(0,1fr)] grid-rows-[auto_minmax(0,1fr)_auto] overflow-hidden"
      >
        <DialogHeader className="min-w-0 pr-6">
          <DialogTitle className="break-all font-mono">{name}</DialogTitle>
          <DialogDescription className="line-clamp-3 break-words">{detail?.description}</DialogDescription>
        </DialogHeader>
        <div className="-mx-4 min-h-0 min-w-0 overflow-y-auto overscroll-contain px-4 sm:-mx-6 sm:px-6">
          {detailQ.isLoading ? <Skeleton className="h-40 w-full" /> : detailQ.isError ? (
            <p className="text-sm text-destructive">{getErrMsg(detailQ.error)}</p>
          ) : detail ? (
            <div className="min-w-0 space-y-3 text-sm">
              <div className="flex min-w-0 flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
                <span>来源：{sourceLabel(detail)}</span>
                {detail.source_url ? <a className="min-w-0 break-all text-primary underline" href={detail.source_url} target="_blank" rel="noreferrer">{detail.source_url}</a> : null}
                {detail.source_ref ? <span>分支：{detail.source_ref}</span> : null}
                {detail.source_commit ? <span>提交：{detail.source_commit.slice(0, 12)}</span> : null}
                {detail.version ? <span>版本：{detail.version}</span> : null}
                {detail.files.length ? <span>{detail.files.length} 个文件</span> : null}
              </div>
              {detail.upstream_changed ? (
                <p className="rounded-md bg-amber-500/10 px-3 py-2 text-xs text-amber-800 dark:text-amber-200">
                  插件升级带来了新的默认内容（{detail.upstream_version || "新版本"}），你的修改没有被覆盖。可以查看差异后恢复默认，或继续保留自己的修改。
                </p>
              ) : null}
              {detail.skipped_files.length ? (
                <p className="text-xs text-muted-foreground">已跳过 {detail.skipped_files.length} 个文件（二进制、过大、符号链接或隐藏文件）。</p>
              ) : null}
              {detail.bundled ? (
                <div role="tablist" aria-label="SKILL.md 视图" className="inline-flex rounded-lg border border-border/70 bg-muted/40 p-0.5 text-xs">
                  <TabButton active={tab === "edit"} onClick={() => setTab("edit")} icon={<SquarePen className="h-3.5 w-3.5" aria-hidden />}>
                    {detail.editable ? "编辑" : "内容"}{dirty ? " ·未保存" : ""}
                  </TabButton>
                  <TabButton active={tab === "diff"} onClick={() => setTab("diff")} icon={<FileDiff className="h-3.5 w-3.5" aria-hidden />}>
                    与默认版本对比
                  </TabButton>
                </div>
              ) : null}
              {!showDiff ? (
                <>
                  <Textarea
                    rows={18}
                    wrap="soft"
                    spellCheck={false}
                    className="min-h-[min(50dvh,28rem)] resize-y whitespace-pre-wrap break-words font-mono text-xs leading-5 [overflow-wrap:anywhere]"
                    readOnly={!detail.editable}
                    value={draft ?? detail.skill_md}
                    onChange={(event) => setDraft(event.target.value)}
                    aria-label="SKILL.md"
                  />
                  {!detail.editable ? (
                    <p className="text-xs text-muted-foreground">
                      {detail.builtin ? "TelePilot 内置领域技能由程序定义，只能停用。" : "来自 GitHub / zip 链接的 skill 以源地址为准，请修改源后“更新”。"}
                    </p>
                  ) : null}
                </>
              ) : upstreamQ.isLoading ? <Skeleton className="h-40 w-full" /> : upstreamQ.isError ? (
                <p className="text-sm text-destructive">{getErrMsg(upstreamQ.error)}</p>
              ) : upstreamQ.data ? (
                <SkillDiffViewer
                  oldText={upstreamQ.data.upstream_skill_md}
                  newText={draft ?? upstreamQ.data.current_skill_md}
                  newLabel={dirty ? "当前编辑（未保存）" : "当前版本（你的修改）"}
                />
              ) : null}
            </div>
          ) : null}
        </div>
        <DialogFooter className="flex-row flex-wrap justify-end gap-2 sm:space-x-0">
          {detail?.editable ? (
            <Button type="button" disabled={busy || !dirty} onClick={() => saveMut.mutate()}>保存修改</Button>
          ) : null}
          {detail?.updatable ? <Button type="button" variant="outline" disabled={busy} onClick={() => updateMut.mutate()}>从源地址更新</Button> : null}
          {detail?.bundled ? (
            <>
              {detail.user_modified ? (
                <Button type="button" variant="outline" disabled={busy} onClick={async () => { if (await confirmDialog({ title: "恢复为默认版本？", description: "会放弃你对这个 Skill 的修改，恢复为插件内置的默认版本。", confirmLabel: "恢复默认", tone: "danger" })) restoreMut.mutate(); }}>恢复默认</Button>
              ) : null}
              {detail.upstream_changed ? <Button type="button" variant="outline" disabled={busy} onClick={() => keepMut.mutate()}>保留我的修改</Button> : null}
            </>
          ) : null}
          {detail?.uninstallable && !detail.builtin ? (
            <Button
              type="button"
              variant="destructive"
              disabled={busy}
              onClick={async () => { if (await confirmDialog({ title: `卸载 ${name}？`, description: "会删除文件，并从所有插件配置中移除；不可恢复。", confirmLabel: "卸载", tone: "danger" })) uninstallMut.mutate(); }}
            >
              卸载
            </Button>
          ) : null}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function TabButton({ active, onClick, icon, children }: { active: boolean; onClick: () => void; icon: React.ReactNode; children: React.ReactNode }) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      onClick={onClick}
      className={cn(
        "inline-flex min-h-8 items-center gap-1.5 rounded-md px-3 py-1 font-medium transition-colors",
        active ? "bg-card text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground",
      )}
    >
      {icon}
      {children}
    </button>
  );
}
