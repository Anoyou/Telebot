import { BookOpen, ShieldCheck } from "lucide-react";

import {
  Card,
  CardContent,
  CardHeader,
} from "@/components/ui/card";
import { SectionHeader } from "@/components/ui/status";
import { CommandBadge } from "@/components/CommandBadge";

export function HowItWorks({ cmdPrefix = ",", defaultOpen = false }: { cmdPrefix?: string; defaultOpen?: boolean }) {
  return (
    <details id="how-it-works" className="group scroll-mt-6" open={defaultOpen}>
      <summary className="cursor-pointer list-none">
        <Card className="transition-colors group-open:border-primary/40">
          <CardHeader className="pb-3">
            <SectionHeader
              icon={BookOpen}
              title="工作原理"
              description={
                <>回复任意 Telegram 消息后发送 <CommandBadge className="mx-1">{cmdPrefix}ai 你的问题</CommandBadge>，worker 会把回答编辑回指令消息。</>
              }
            />
          </CardHeader>
          <CardContent className="hidden space-y-3 text-sm group-open:block">
            <ol className="list-decimal space-y-1.5 pl-5 text-muted-foreground">
              <li>
                前缀来自服务器设置里的指令前缀 <CommandBadge>{cmdPrefix}</CommandBadge>，不是固定的 <CommandBadge>/</CommandBadge>。
              </li>
              <li>worker 只拦截自己发出的 outgoing 指令，别人发送同样指令不会触发。</li>
              <li>
                被回复消息正文加上指令后的问题会拼成 user prompt，模板里的 <code>system_prompt</code> 负责设定回答风格。
              </li>
              <li>
                返回时编辑你的指令消息；末尾会附上模型名、in/out tokens，自动路由会额外标出决策原因。
              </li>
              <li>完整配置路径是先添加模型提供商，再创建 type=ai 的指令模板，最后到账号详情启用指令。</li>
            </ol>
            <div className="rounded-md border px-3 py-2 text-xs alert-info">
              <p className="font-semibold">调用示例</p>
              <p className="mt-1">
                <CommandBadge>{cmdPrefix}ai 总结这段话</CommandBadge>、<CommandBadge>{cmdPrefix}ai search 查一下版本更新</CommandBadge>、
                <CommandBadge>{cmdPrefix}ai image 画一张极简图标</CommandBadge>；也可以新建直接指令
                <CommandBadge className="mx-1">{cmdPrefix}search</CommandBadge> 或 <CommandBadge>{cmdPrefix}image</CommandBadge>。
              </p>
            </div>
            <div className="flex items-start gap-2 rounded-md border px-3 py-2 text-xs alert-warning">
              <ShieldCheck className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              <span>API Key 经主密钥 Fernet 加密落库；GET 接口不返回明文，错误信息也会剥离敏感 token。</span>
            </div>
          </CardContent>
        </Card>
      </summary>
    </details>
  );
}
