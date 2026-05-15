import { AISettings } from "@/pages/AISettings";

// F3 先复用 AISettings 的路由策略/说明内容，后续可将 guide/glossary/recommend 拆为独立组件。
export function AIRouting() {
  return <AISettings />;
}
