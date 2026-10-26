import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { listAgentSkills, pluginSkillOptions } from "@/api/agentSkills";
import { AGENT_SKILLS_WEB_ENTRY, AGENT_SKILLS_WEB_PATH } from "@/lib/agentSkillOptions";
import { getErrMsg } from "@/lib/api";
import { Label } from "@/components/ui/label";
import { Spinner } from "@/components/ui/misc";
import { Switch } from "@/components/ui/switch";

/**
 * 插件配置 `x-ui-widget: agent-skill-multi-select`：从已安装且“对插件启用”的 Agent Skills 中多选。
 * 已保存但当前不可用（被停用或卸载）的名称如实保留显示，由用户决定是否取消。
 */
export function AgentSkillMultiSelectField({
  inputId,
  label,
  description,
  value,
  onChange,
}: {
  inputId: string;
  label: string;
  description?: string;
  value: unknown;
  onChange: (v: unknown) => void;
}) {
  const query = useQuery({ queryKey: ["agent-skills"], queryFn: listAgentSkills, staleTime: 30_000 });
  const selected = Array.isArray(value) ? value.map((item) => String(item)) : [];
  const options = pluginSkillOptions(query.data?.items);
  const known = new Set(options.map((option) => option.value));
  const stale = selected.filter((name) => !known.has(name));

  const toggle = (name: string, checked: boolean) => {
    const next = checked ? [...selected.filter((item) => item !== name), name] : selected.filter((item) => item !== name);
    onChange(next);
  };

  return (
    <div className="space-y-1.5">
      <Label htmlFor={inputId}>{label}</Label>
      {description && <p className="text-xs text-muted-foreground">{description}</p>}
      {query.isLoading ? (
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <Spinner className="h-3.5 w-3.5" /> 正在读取已安装的 skills…
        </div>
      ) : query.isError ? (
        <p className="text-xs text-destructive">读取 skills 失败：{getErrMsg(query.error)}</p>
      ) : null}
      <div id={inputId} className="grid gap-2 sm:grid-cols-2">
        {options.map((option) => (
          <div key={option.value} className="flex min-h-10 items-start gap-2 rounded-md border bg-background px-3 py-2 text-sm">
            <Switch
              className="mt-0.5 scale-90"
              checked={selected.includes(option.value)}
              aria-label={`${option.label} 已选择`}
              onCheckedChange={(checked) => toggle(option.value, checked)}
            />
            <span className="min-w-0">
              <span className="block break-words font-medium">{option.label}</span>
              <span className="mt-0.5 block break-words text-xs text-muted-foreground">{option.description}</span>
            </span>
          </div>
        ))}
        {stale.map((name) => (
          <div key={name} className="flex min-h-10 items-start gap-2 rounded-md border border-dashed bg-background px-3 py-2 text-sm">
            <Switch className="mt-0.5 scale-90" checked aria-label={`${name} 已选择`} onCheckedChange={(checked) => toggle(name, checked)} />
            <span className="min-w-0">
              <span className="block break-words font-medium">{name}</span>
              <span className="mt-0.5 block text-xs text-amber-700 dark:text-amber-300">当前不可用：已停用对插件的使用或已卸载</span>
            </span>
          </div>
        ))}
      </div>
      {!query.isLoading && !query.isError && options.length === 0 && stale.length === 0 ? (
        <p className="text-xs text-muted-foreground">
          还没有对插件启用的 skill。可在 <Link className="text-primary underline" to={AGENT_SKILLS_WEB_PATH}>{AGENT_SKILLS_WEB_ENTRY}</Link> 安装并打开“插件可用”。
        </p>
      ) : null}
    </div>
  );
}
