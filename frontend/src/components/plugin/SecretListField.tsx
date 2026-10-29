import { useState } from "react";
import { ArrowDown, ArrowUp, Plus, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

// x-secret-list：密钥列表控件（契约控件 secret-list）。已保存的密钥只显示服务器给的遮罩，
// 可改备注、删除、排序；新增时填明文，保存后由服务器加密，下次读取同样只返回遮罩。

interface SecretListSpec {
  secretField: string;
  noteField: string;
  idField: string;
}

interface SecretListFieldSchema {
  "x-secret-list"?: unknown;
  items?: { properties?: Record<string, unknown> };
}

export function secretListSpec(field: SecretListFieldSchema): SecretListSpec {
  const raw = field["x-secret-list"];
  const declared = raw && typeof raw === "object" ? (raw as Record<string, unknown>) : {};
  let secretField = typeof declared.secret_field === "string" ? declared.secret_field : "";
  if (!secretField) {
    const props = field.items?.properties ?? {};
    secretField = ["token", "secret", "api_key", "key", "value"].find((key) => key in props) ?? "token";
  }
  return {
    secretField,
    noteField: typeof declared.note_field === "string" && declared.note_field ? declared.note_field : "note",
    idField: typeof declared.id_field === "string" && declared.id_field ? declared.id_field : "secret_id",
  };
}

/** 新增输入：JSON（插件导入格式）整体作为一项，其余按行拆分。 */
export function splitSecretInput(raw: string): string[] {
  const text = raw.trim();
  if (!text) return [];
  if (text.startsWith("{") || text.startsWith("[")) return [text];
  return text.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
}

export function SecretListField({
  inputId,
  label,
  description,
  field,
  value,
  onChange,
}: {
  inputId: string;
  label: string;
  description?: string;
  field: SecretListFieldSchema;
  value: unknown;
  onChange: (value: unknown) => void;
}) {
  const spec = secretListSpec(field);
  const items = Array.isArray(value)
    ? value.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object")
    : [];
  const [draft, setDraft] = useState("");
  const [draftNote, setDraftNote] = useState("");

  const update = (next: Record<string, unknown>[]) => onChange(next);
  const move = (index: number, delta: number) => {
    const target = index + delta;
    if (target < 0 || target >= items.length) return;
    const next = [...items];
    const [moved] = next.splice(index, 1);
    next.splice(target, 0, moved);
    update(next);
  };
  const add = () => {
    const secrets = splitSecretInput(draft);
    if (secrets.length === 0) return;
    update([...items, ...secrets.map((secret) => ({ [spec.secretField]: secret, [spec.noteField]: draftNote.trim() }))]);
    setDraft("");
    setDraftNote("");
  };

  return (
    <div className="space-y-2" data-secret-list>
      <div className="space-y-1.5">
        <Label htmlFor={inputId}>{label}</Label>
        {description && <p className="text-xs text-muted-foreground">{description}</p>}
        <p className="text-xs text-muted-foreground">
          已保存 {items.length} 个。出于安全只显示遮罩；删除或调整顺序后点保存生效。
        </p>
      </div>
      {items.length > 0 ? (
        <ul className="space-y-2">
          {items.map((item, index) => {
            const saved = Boolean(item[spec.idField]);
            const secret = String(item[spec.secretField] ?? "");
            return (
              <li
                key={`${String(item[spec.idField] ?? "")}-${index}`}
                className="grid gap-2 rounded-md border bg-background p-2 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto]"
                data-secret-list-item={index}
              >
                <Input
                  value={saved ? secret : `${secret.slice(0, 4)}…（新增，保存后加密）`}
                  readOnly
                  aria-label={`第 ${index + 1} 个${label}`}
                  className="font-mono text-xs"
                />
                <Input
                  value={String(item[spec.noteField] ?? "")}
                  placeholder="备注（可选）"
                  aria-label={`第 ${index + 1} 个备注`}
                  onChange={(event) => {
                    const next = [...items];
                    next[index] = { ...item, [spec.noteField]: event.target.value };
                    update(next);
                  }}
                />
                <div className="flex items-center gap-1">
                  <Button type="button" variant="ghost" size="icon" aria-label="上移" disabled={index === 0} onClick={() => move(index, -1)}>
                    <ArrowUp className="h-4 w-4" />
                  </Button>
                  <Button type="button" variant="ghost" size="icon" aria-label="下移" disabled={index === items.length - 1} onClick={() => move(index, 1)}>
                    <ArrowDown className="h-4 w-4" />
                  </Button>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    aria-label={`删除第 ${index + 1} 个`}
                    onClick={() => update(items.filter((_, position) => position !== index))}
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </div>
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="rounded-md border border-dashed p-3 text-xs text-muted-foreground">还没有保存任何{label}。</p>
      )}
      <div className="grid gap-2 rounded-md border border-dashed p-2">
        <Textarea
          id={inputId}
          value={draft}
          rows={2}
          placeholder="新增：每行一个；也可以粘贴插件支持的导入格式（如 JSON）"
          onChange={(event) => setDraft(event.target.value)}
        />
        <div className="flex flex-col gap-2 sm:flex-row">
          <Input value={draftNote} placeholder="备注（可选）" aria-label="新增备注" onChange={(event) => setDraftNote(event.target.value)} />
          <Button type="button" variant="outline" disabled={!draft.trim()} onClick={add}>
            <Plus className="mr-1 h-4 w-4" />
            添加
          </Button>
        </div>
      </div>
    </div>
  );
}
